# Hosted authentication providers

Hosted authentication uses installed entry points in the `artifact_studio.auth` group. An `AuthProvider` implements async `authenticate(headers) -> Principal`; the provider verifies identity, while core server code always enforces membership, host, origin and route policy. A provider cannot grant access by bypassing those checks. Cloudflare Access is the first adapter: it verifies the signed JWT at the origin against the configured team and audience. Configure exact human emails and agent service-token Client IDs in the hosted configuration; the two principal types have different route permissions.

See [hosted setup](HOSTED.md) for the browser MFA policy, service-token headers and separate preview host. Generic OAuth and native agent OAuth are future work, not supported setup paths. Provider and deployment behavior still require live-account validation.

Providers are trusted server code, not sandboxed extensions. They run with the
server's privileges and are responsible for truthful identity verification. The
contract separates identity verification from the application's access policy;
installing an untrusted provider would compromise that boundary.

## Installing another provider

A provider entry point names a factory called with the `auth_options` mapping. It must validate credentials using a real verification library and trusted, operator-configured issuer and keys before returning a principal. Authentication errors must reject the request; never trust unsigned headers or token claims. The application enforces configured membership and route permissions after authentication.

For example, a separately installed package can register its factory in `pyproject.toml`:

```toml
[project.entry-points."artifact_studio.auth"]
example = "example_auth:create_provider"
```

```python
from artifact_studio.authentication import AuthenticationError, Principal

def create_provider(options):
    return ExampleProvider(options)

class ExampleProvider:
    def __init__(self, options):
        self.verifier = make_verified_token_verifier(options)  # Your library and trusted issuer/keys.

    async def authenticate(self, headers):
        claims = await self.verifier.verify(headers)  # Must reject invalid or expired signatures and claims.
        if not claims.subject:
            raise AuthenticationError("Missing subject")
        return Principal(subject=claims.subject, email=claims.email, service=claims.service)
```

`make_verified_token_verifier` is a placeholder for your provider's actual implementation, not a supplied function. Install the package in the server environment. The stock Docker image installs its locked dependencies during build; build a custom image that installs your package into that environment and test provider loading before deployment. Keep the provider package and its dependencies pinned and reviewed.
