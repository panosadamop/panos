import pytest

from panos_core.redaction import REDACTED, redact, redact_text

pytestmark = pytest.mark.req("NFR-SEC-004")

SECRETS = [
    "sk-ant-api03-" + "A" * 40,
    "sk-proj-" + "b" * 40,
    "sk-" + "c" * 48,
    "ghp_" + "d" * 36,
    "github_pat_" + "e" * 60,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "AIza" + "f" * 35,
    "xoxb-1234567890-abcdefghij",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_known_secret_formats_are_redacted(secret: str) -> None:
    out = redact_text(f"value is {secret} here")
    assert secret not in out
    assert REDACTED in out


def test_bearer_and_basic_headers_redacted() -> None:
    assert redact_text("Authorization: Bearer abc.def-ghi_jkl") == (
        f"Authorization: Bearer {REDACTED}"
    )
    assert "dXNlcjpwYXNz" not in redact_text("Basic dXNlcjpwYXNz")


def test_credentials_in_urls_redacted_but_url_kept() -> None:
    out = redact_text("postgres://admin:hunter2@db.local:5432/app")
    assert "hunter2" not in out
    assert out.startswith("postgres://admin:") and "@db.local:5432/app" in out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=hunter2", "hunter2"),
        ("api_key: 'abc123xyz'", "abc123xyz"),
        ("TOKEN=qwertyuiop", "qwertyuiop"),
        ('secret = "s3cr3t"', "s3cr3t"),
    ],
)
def test_key_value_assignments_redacted(text: str, secret: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert REDACTED in out


def test_private_key_block_redacted() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    assert redact_text(f"key:\n{pem}\nend") == f"key:\n{REDACTED}\nend"


def test_sensitive_keys_redacted_recursively() -> None:
    data = {
        "user": "panos",
        "password": "hunter2",
        "nested": {"api_key": "zzz", "items": [{"token": "t1"}, "plain"]},
        "authorization": "Bearer x",
    }
    out = redact(data)
    assert out["user"] == "panos"
    assert out["password"] == REDACTED
    assert out["nested"]["api_key"] == REDACTED
    assert out["nested"]["items"][0]["token"] == REDACTED
    assert out["nested"]["items"][1] == "plain"
    assert out["authorization"] == REDACTED


def test_numeric_and_non_secret_fields_untouched() -> None:
    data = {"max_tokens": 4096, "author": "Jane", "tokens_used": 12, "event": "ok", "flag": True}
    assert redact(data) == data


def test_input_is_not_mutated() -> None:
    data = {"password": "x", "list": ["sk-ant-" + "a" * 20]}
    redact(data)
    assert data == {"password": "x", "list": ["sk-ant-" + "a" * 20]}


def test_tuples_and_sets_keep_their_type() -> None:
    assert isinstance(redact(("a", "b")), tuple)
    assert isinstance(redact({"a"}), set)


# ------------------------------------------------------------------ redaction v2 (M2-09)
@pytest.mark.parametrize(
    "secret",
    [
        "glpat-" + "a1B2c3D4e5F6g7H8i9J0",
        "npm_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5",
        "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
        "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
        "SG." + "ngeVfQFYQlKU0ufo8x5d1A" + "." + "TwL2iGABf9DHoTf-09kqeF8tAmbihYzrnopKc-1s5cr",
    ],
)
def test_more_provider_formats_redacted(secret: str) -> None:
    assert secret not in redact_text(f"x {secret} y")


def test_azure_connection_string_key_redacted() -> None:
    text = "DefaultEndpointsProtocol=https;AccountName=acc;AccountKey=abc+/123==;EndpointSuffix=x"
    out = redact_text(text)
    assert "abc+/123==" not in out
    assert "AccountName=acc" in out


def test_random_high_entropy_tokens_are_redacted() -> None:
    import secrets

    tokens = [secrets.token_urlsafe(32) for _ in range(200)]
    leaked = [t for t in tokens if t in redact_text(f"v {t}")]
    # The entropy layer is heuristic: allow a tiny miss rate on random tokens.
    assert len(leaked) <= 4


@pytest.mark.parametrize(
    "benign",
    [
        "3f786850e387550fdab836ed7e6dc881de23001b",  # git commit SHA
        "1cd6a5bf58f74ab98983d31e90cdb216",  # uuid hex / correlation id
        "prj_a25519fbdf594188bdfa9a5ea72959fc",  # Panos id
        "ThisIsAVeryLongCamelCaseIdentifierName2026ForTesting",
        "test_should_return_404_when_user_not_found_in_db",
        "SpringBootApplicationContextConfiguration2Test",
        "C:/Users/padamop/PycharmProjects/panos/packages/panos-core/src/panos_core",
        "The quick brown fox jumps over the lazy dog 1234567890 times.",
    ],
)
def test_entropy_layer_spares_ordinary_text(benign: str) -> None:
    assert redact_text(benign) == benign


def test_entropy_layer_can_be_disabled() -> None:
    import secrets

    token = secrets.token_urlsafe(32)
    assert redact_text(token, entropy=False) == token
