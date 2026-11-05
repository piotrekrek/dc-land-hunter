"""OAuth storage for the Claude connector (PLAN.md, "Claude connector (MCP)", Task 2).

`users.mcp_access` is the admin's switch: only a user with it on can connect Claude, and switching it off kills
the user's tokens at once (the token lookup joins `users`). Clients register themselves (RFC 7591), so
`oauth_clients` holds whatever the SDK stores. Requests (the consent step), codes and tokens keep only the SHA-256
of the secret value, like `sessions`; the clear value exists only in the browser redirect or in Claude.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users ADD COLUMN mcp_access boolean NOT NULL DEFAULT false;

        CREATE TABLE oauth_clients (
            client_id   text PRIMARY KEY,
            info        jsonb NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE oauth_requests (
            id_hash     text PRIMARY KEY,
            client_id   text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
            params      jsonb NOT NULL,
            expires_at  timestamptz NOT NULL
        );

        CREATE TABLE oauth_codes (
            code_hash   text PRIMARY KEY,
            client_id   text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
            user_id     integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            params      jsonb NOT NULL,
            expires_at  timestamptz NOT NULL
        );

        CREATE TABLE oauth_tokens (
            token_hash  text PRIMARY KEY,
            kind        text NOT NULL CHECK (kind IN ('access', 'refresh')),
            client_id   text NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
            user_id     integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            scopes      text[] NOT NULL,
            resource    text,
            expires_at  timestamptz NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX oauth_tokens_user_ix ON oauth_tokens (user_id);
        CREATE INDEX oauth_tokens_client_ix ON oauth_tokens (client_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS oauth_tokens, oauth_codes, oauth_requests, oauth_clients;
        ALTER TABLE users DROP COLUMN IF EXISTS mcp_access;
        """
    )
