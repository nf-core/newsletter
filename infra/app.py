#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.newsletter_stack import NfCoreNewsletterStack

app = cdk.App()
NfCoreNewsletterStack(
    app,
    "NfCoreNewsletterStack",
    env=cdk.Environment(account="728131696474", region="eu-west-1"),
)
app.synth()
