# Security policy

## Reporting vulnerabilities

Please report security issues privately to `philipp@dreimann.net`. Do not include device
passwords, firmware images, backups, private addresses, or diagnostic logs in a public issue.

## Network threat model

Gira provisioning protocols were designed for trusted local networks. Some device operations
use plaintext HTTP Basic authentication. Other services use certificates that are not normally
trusted by the public CA ecosystem, so TLS verification remains disabled by default for
device-compatible behavior.

Use pygira only from a trusted management network. Do not expose device management ports to
the internet or an untrusted client network. GDS library clients can opt into system CA
verification or provide an `ssl.SSLContext`. Configuration-service callers can additionally
provide a custom CA context and a SHA-256 leaf-certificate fingerprint through `TlsConfig`.
Fingerprint mismatches fail closed. Pins must be reviewed and updated after legitimate device
certificate replacement or firmware changes.

## Stored credentials

`devices.toml` stores credentials in plaintext. Files created by `pygira config` use atomic
replacement and owner-only permissions on POSIX systems, but filesystem backups and privileged
users can still read them. Never commit or synchronize the file, and use a dedicated device
account with the least privilege supported by the device.

Hardware integration tests read credentials only from environment variables after an explicit
`PYGIRA_HARDWARE_TESTS=1` opt-in. Do not place those variables in tracked files or CI logs.
