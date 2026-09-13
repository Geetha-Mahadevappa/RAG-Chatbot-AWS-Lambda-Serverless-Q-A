# Real login, not anonymous access: a Cognito User Pool (username/password accounts) federates
# into the Identity Pool as the ONLY identity source (allow_unauthenticated_identities = false).
# Only a signed-in user gets AWS credentials scoped to invoking the Q&A Lambda -- unlike an
# unauthenticated Identity Pool, someone reading the frontend's public JS can't just replicate
# two REST calls to get their own credentials; they'd need actual account credentials.
#
# No public self-service sign-up (admin_create_user_config.allow_admin_create_user_only):
# accounts are created by the account owner via `aws cognito-idp admin-create-user` (see
# README) -- appropriate for a single/few-user demo, avoids building email verification flows.
#
# Frontend flow (frontend/index.html): InitiateAuth (USER_PASSWORD_AUTH, plain unauthenticated
# fetch -- Cognito doesn't evaluate IAM policy for this API) -> IdToken -> fed into the
# Identity Pool's GetId/GetCredentialsForIdentity Logins map -> SigV4-sign the Lambda call.

resource "aws_cognito_user_pool" "this" {
  name = "${var.project_name}-users"

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }
}

resource "aws_cognito_user_pool_client" "frontend" {
  name         = "${var.project_name}-frontend-client"
  user_pool_id = aws_cognito_user_pool.this.id

  # No client secret: a browser app can't keep one confidential. USER_PASSWORD_AUTH enables
  # plain username+password login via InitiateAuth (vs. ALLOW_USER_SRP_AUTH's SRP handshake,
  # more crypto than is worth hand-rolling in a no-build static page); REFRESH_TOKEN_AUTH lets
  # the frontend silently renew the ID token instead of forcing re-login every hour.
  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

resource "aws_cognito_identity_pool" "this" {
  identity_pool_name               = "${var.project_name} frontend pool"
  allow_unauthenticated_identities = false

  cognito_identity_providers {
    client_id     = aws_cognito_user_pool_client.frontend.id
    provider_name = "cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
  }
}

data "aws_iam_policy_document" "cognito_authenticated_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = ["cognito-identity.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "cognito-identity.amazonaws.com:aud"
      values   = [aws_cognito_identity_pool.this.id]
    }
    condition {
      test     = "ForAnyValue:StringLike"
      variable = "cognito-identity.amazonaws.com:amr"
      values   = ["authenticated"]
    }
  }
}

resource "aws_iam_role" "cognito_authenticated" {
  name               = "${var.project_name}-cognito-authenticated-role"
  assume_role_policy = data.aws_iam_policy_document.cognito_authenticated_assume.json
}

# Same-account AWS_IAM Function URL access only needs an identity-based policy on the caller
# (confirmed against current AWS docs) -- both actions are required as of an Oct 2025 AWS
# scope change. No resource-based aws_lambda_permission needed for same-account callers.
data "aws_iam_policy_document" "cognito_authenticated_permissions" {
  statement {
    sid       = "InvokeQaFunctionUrlOnly"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"]
    resources = [aws_lambda_function.qa.arn]
  }
}

resource "aws_iam_role_policy" "cognito_authenticated" {
  name   = "${var.project_name}-cognito-authenticated-policy"
  role   = aws_iam_role.cognito_authenticated.id
  policy = data.aws_iam_policy_document.cognito_authenticated_permissions.json
}

resource "aws_cognito_identity_pool_roles_attachment" "this" {
  identity_pool_id = aws_cognito_identity_pool.this.id
  roles = {
    authenticated = aws_iam_role.cognito_authenticated.arn
  }
}
