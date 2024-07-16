# SPDX-License-Identifier: Apache-2.0

"""Workflow Engine authentication."""

import os
from urllib.parse import urlencode

import jwt
import requests
from cryptography.fernet import Fernet
from keycloak import KeycloakOpenID
from tornado import escape
from tornado.auth import OAuth2Mixin
from tornado.web import RequestHandler

AUTH_HOST = os.environ["KC_HOST"]
CLIENT_ID = os.environ["KC_CLIENT_ID"]
REALM = os.environ["KC_REALM"]
SECRET = os.environ["KC_SCR"]
REDIRECT_URI = os.environ["REDIRECT_URI"]

USER_INFO = f"{AUTH_HOST}/auth/realms/{REALM}/protocol/openid-connect/userinfo"

CRYPT = Fernet(Fernet.generate_key())

KEYCLOAK = KeycloakOpenID(
    server_url=f"{AUTH_HOST}/auth/", client_id=CLIENT_ID, client_secret_key=SECRET, realm_name=REALM
)


class KeycloakOAuth2Mixin(OAuth2Mixin):
    """Keycloak authentication using OAuth2."""

    _OAUTH_AUTHORIZE_URL = f"{AUTH_HOST}/auth/realms/{REALM}/protocol/openid-connect/auth"
    _OAUTH_ACCESS_TOKEN_URL = f"{AUTH_HOST}/auth/realms/{REALM}/protocol/openid-connect/token"
    _OAUTH_LOGOUT_URL = f"{AUTH_HOST}/auth/realms/{REALM}/protocol/openid-connect/logout"
    _OAUTH_USERINFO_URL = USER_INFO

    async def get_authenticated_user(self, redirect_uri, code, client_id, client_secret):
        """Handle the login, returning an access token."""
        http = self.get_auth_http_client()
        body = urlencode(
            {
                "redirect_uri": redirect_uri,
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "openid",
                "grant_type": "authorization_code",
            }
        )
        response = await http.fetch(
            self._OAUTH_ACCESS_TOKEN_URL,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
        )
        return escape.json_decode(response.body)


class KeycloakAuthHandler(RequestHandler, KeycloakOAuth2Mixin):
    """Auth request handler."""

    # pylint: disable=abstract-method

    def get_current_user(self):
        return self.get_signed_cookie("user")

    async def get(self):
        """."""
        url = self.get_argument("url", None)
        if self.current_user:
            if url is not None:
                self.redirect(url)
            else:
                self.set_status(204)
        else:
            code = self.get_argument("code", None)
            if code is not None:
                user = await self.get_authenticated_user(
                    REDIRECT_URI % url, code, CLIENT_ID, SECRET
                )
                token = user["refresh_token"]
                token_info = jwt.decode(token, options={"verify_signature": False})
                client_id = token_info["azp"]
                if client_id != CLIENT_ID:
                    raise ValueError("Invalid client id")
                assert token_info["typ"] == "Offline"
                self.set_signed_cookie("user", CRYPT.encrypt(token.encode()))
                if url is not None:
                    self.redirect(url)
            else:
                self.authorize_redirect(REDIRECT_URI % url, CLIENT_ID)


def _validate_token(access_token):
    """Validate token."""
    response = requests.get(USER_INFO, headers={"authorization": access_token}, timeout=10)
    response.raise_for_status()
