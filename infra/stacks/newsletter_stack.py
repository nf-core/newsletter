"""CDK stack for the nf-core newsletter distribution service.

Phase 1 (email) only. Fully serverless / event-driven:

  * Amazon SES owns the contact list, the unsubscribe page, suppression, and
    bounce/complaint handling. We add only what SES lacks: a double-opt-in flow.
  * API Gateway HTTP API + two Lambdas (subscribe, confirm) for double opt-in.
  * EventBridge Scheduler (monthly) -> send Lambda, which fetches the rendered
    /newsletter/<y>/<m>/email HTML from nf-co.re and SendEmail's it to every
    contact subscribed to the `monthly-newsletter` topic.

There is deliberately NO DynamoDB table and NO bounce/complaint Lambda: SES list
management + account-level suppression cover contacts, unsubscribe, and
bounce/complaint handling.

Secrets are pre-created SSM SecureString parameters under /nf-core-newsletter/*,
referenced here (created manually outside CDK, same convention as the slackbot).
"""

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_scheduler as scheduler,
    aws_ses as ses,
)
from constructs import Construct

# Repo root /src — the Lambda code asset. Handlers import only boto3 (already in
# the Lambda runtime) and the stdlib, so the asset needs no bundling step.
SRC_DIR = Path(__file__).parents[2] / "src"

# ── Tunables (plain config; secrets live in SSM, see below) ──────────────────
CONTACT_LIST_NAME = "nf-core-newsletter"
TOPIC_NAME = "monthly-newsletter"
CONFIGURATION_SET_NAME = "nf-core-newsletter"

# From address / sending domain. The identity must be verified in SES by hand
# (see the README "Prerequisites"); this stack does NOT create or verify it.
FROM_ADDRESS = "nf-core newsletter <newsletter@nf-co.re>"
SENDING_IDENTITY = "nf-co.re"  # the verified SES identity that authorises sends

WEBSITE_BASE_URL = "https://nf-co.re"
RSS_URL = "https://nf-co.re/newsletter/rss.xml"
ALLOWED_ORIGIN = "https://nf-co.re"  # CORS origin for the sign-up form

# nf-core/newsletter logo for the confirm email + landing page (white background
# baked into the PNG so it reads in dark mode). Served from the website's public/
# dir; resolves once the website newsletter pages are live on nf-co.re.
LOGO_URL = "https://nf-co.re/images/logo/nf-core-newsletter-lightbg.png"

# Pre-created SSM SecureString — HMAC key used to sign/verify confirm tokens.
CONFIRM_TOKEN_SECRET_PARAM = "/nf-core-newsletter/CONFIRM_TOKEN_SECRET"

# Monthly send: 09:00 UTC on the 1st of every month.
SEND_SCHEDULE = "cron(0 9 1 * ? *)"

# Per-recipient send pace (emails/sec); matches the SES account max send rate.
SEND_RATE_PER_SEC = "14"


class NfCoreNewsletterStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        Tags.of(self).add("Project", "nf-core-newsletter")

        # ── SES contact list + monthly-newsletter topic ─────────────────────
        # SES allows one contact list per account; this stack owns it. New
        # contacts default to OPT_OUT on the topic so an unconfirmed sign-up
        # never receives anything — the confirm Lambda flips them to OPT_IN.
        contact_list = ses.CfnContactList(
            self,
            "ContactList",
            contact_list_name=CONTACT_LIST_NAME,
            description="nf-core newsletter subscribers",
            topics=[
                ses.CfnContactList.TopicProperty(
                    topic_name=TOPIC_NAME,
                    display_name="nf-core monthly newsletter",
                    default_subscription_status="OPT_OUT",
                    description="One email a month with nf-core community news.",
                )
            ],
        )
        contact_list.apply_removal_policy(RemovalPolicy.RETAIN)

        # ── SES configuration set ────────────────────────────────────────────
        # Attached to every SendEmail so engagement/bounce metrics are tracked
        # and (later) event destinations can be added without touching senders.
        ses.ConfigurationSet(self, "ConfigSet", configuration_set_name=CONFIGURATION_SET_NAME)

        # ── Shared Lambda config ─────────────────────────────────────────────
        code = lambda_.Code.from_asset(str(SRC_DIR), exclude=["**/__pycache__", "**/*.pyc"])
        common_env = {
            "CONTACT_LIST_NAME": CONTACT_LIST_NAME,
            "TOPIC_NAME": TOPIC_NAME,
            "CONFIGURATION_SET_NAME": CONFIGURATION_SET_NAME,
            "FROM_ADDRESS": FROM_ADDRESS,
            "WEBSITE_BASE_URL": WEBSITE_BASE_URL,
            "RSS_URL": RSS_URL,
            "ALLOWED_ORIGIN": ALLOWED_ORIGIN,
            "LOGO_URL": LOGO_URL,
            "CONFIRM_TOKEN_SECRET_PARAM": CONFIRM_TOKEN_SECRET_PARAM,
            # Per-recipient send pace; matches the SES account max send rate.
            "SEND_RATE_PER_SEC": SEND_RATE_PER_SEC,
        }

        def make_fn(name: str, handler: str, *, timeout: Duration, memory: int = 256) -> lambda_.Function:
            return lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                code=code,
                handler=handler,
                environment=dict(common_env),
                timeout=timeout,
                memory_size=memory,
                log_retention=logs.RetentionDays.ONE_MONTH,
            )

        subscribe_fn = make_fn(
            "SubscribeFn", "nf_core_newsletter.handlers.subscribe.handler", timeout=Duration.seconds(15)
        )
        confirm_fn = make_fn("ConfirmFn", "nf_core_newsletter.handlers.confirm.handler", timeout=Duration.seconds(15))
        send_fn = make_fn(
            "SendFn",
            "nf_core_newsletter.handlers.send.handler",
            timeout=Duration.minutes(15),
            memory=512,
        )

        # ── IAM ──────────────────────────────────────────────────────────────
        contact_list_arn = f"arn:aws:ses:{self.region}:{self.account}:contact-list/{CONTACT_LIST_NAME}"
        send_resources = [
            f"arn:aws:ses:{self.region}:{self.account}:identity/{SENDING_IDENTITY}",
            f"arn:aws:ses:{self.region}:{self.account}:configuration-set/{CONFIGURATION_SET_NAME}",
        ]

        def grant_contact(fn: lambda_.Function, actions: list[str]) -> None:
            fn.add_to_role_policy(iam.PolicyStatement(actions=actions, resources=[contact_list_arn]))

        def grant_send(fn: lambda_.Function, *, list_management: bool = False) -> None:
            # With ListManagementOptions set, SES also authorises SendEmail against
            # the contact-list resource, so the monthly send needs it added.
            resources = [*send_resources, contact_list_arn] if list_management else send_resources
            fn.add_to_role_policy(iam.PolicyStatement(actions=["ses:SendEmail"], resources=resources))

        def grant_token_secret(fn: lambda_.Function) -> None:
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:parameter{CONFIRM_TOKEN_SECRET_PARAM}",
                    ],
                )
            )

        # subscribe: create/update an unconfirmed contact + send the confirm email
        grant_contact(subscribe_fn, ["ses:CreateContact", "ses:GetContact", "ses:UpdateContact"])
        grant_send(subscribe_fn)
        grant_token_secret(subscribe_fn)

        # confirm: verify the token, flip the contact to OPT_IN on the topic
        grant_contact(confirm_fn, ["ses:GetContact", "ses:UpdateContact"])
        grant_token_secret(confirm_fn)

        # send: list subscribed contacts + SendEmail each edition (with list management)
        grant_contact(send_fn, ["ses:ListContacts", "ses:GetContact"])
        grant_send(send_fn, list_management=True)

        # ── HTTP API (double opt-in only) ────────────────────────────────────
        # CORS allows the production origin; an extra origin (e.g. a website
        # deploy-preview) can be added for testing without committing it:
        #   cdk deploy -c extra_cors_origin=https://deploy-preview-N--....netlify.app
        cors_origins = [ALLOWED_ORIGIN]
        extra_origin = self.node.try_get_context("extra_cors_origin")
        if extra_origin:
            cors_origins.append(extra_origin)

        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name="nf-core-newsletter",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=cors_origins,
                allow_methods=[apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.GET],
                allow_headers=["content-type"],
            ),
        )
        http_api.add_routes(
            path="/subscribe",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration("SubscribeIntegration", subscribe_fn),
        )
        http_api.add_routes(
            path="/confirm",
            methods=[apigwv2.HttpMethod.GET],
            integration=apigwv2_integrations.HttpLambdaIntegration("ConfirmIntegration", confirm_fn),
        )

        # The subscribe Lambda builds confirmation links pointing back at /confirm.
        subscribe_fn.add_environment("CONFIRM_URL_BASE", f"{http_api.api_endpoint}/confirm")

        # ── Monthly send schedule (EventBridge Scheduler) ────────────────────
        scheduler_role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        send_fn.grant_invoke(scheduler_role)
        scheduler.CfnSchedule(
            self,
            "MonthlySend",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression=SEND_SCHEDULE,
            schedule_expression_timezone="UTC",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=send_fn.function_arn,
                role_arn=scheduler_role.role_arn,
            ),
        )

        # ── Outputs ──────────────────────────────────────────────────────────
        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "SubscribeUrl", value=f"{http_api.api_endpoint}/subscribe")
        CfnOutput(self, "ContactListName", value=CONTACT_LIST_NAME)
        CfnOutput(self, "TopicName", value=TOPIC_NAME)
        CfnOutput(self, "ConfigurationSetName", value=CONFIGURATION_SET_NAME)
