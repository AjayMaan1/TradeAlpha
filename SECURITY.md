# Security Policy

TradeAlpha is a **local, single-user** application. By default it is reachable only from the machine it runs on; when you
expose it on a network, authentication is required (see below).

## Secure defaults (v1.2.1+)
- Binds to `127.0.0.1` and runs **without** the Werkzeug debugger. `TRADEALPHA_DEBUG=1` is refused on a
  non-loopback `TRADEALPHA_HOST`.
- The OpenAlgo API key is **write-only** in the UI/API (only "configured" + last 4 characters are ever returned).
  In the v2 metadata DB it is encrypted at rest (Fernet; key in the OS keyring or `instance/fernet.key`, mode 0600).
- `SECRET_KEY` is generated per installation (`instance/secret_key`, mode 0600) unless you provide a real one.
- Never commit API keys. CI runs gitleaks on every push.

## Browser-based attacks on the local app (v2)
- **DNS rebinding** is refused: requests whose `Host` is a name other than a loopback name, the bind address or a
  name in `TRADEALPHA_ALLOWED_HOSTS` get 403 (IP addresses such as `http://192.168.1.5:5001` are always accepted -
  rebinding needs a DNS name; set `TRADEALPHA_ALLOWED_HOSTS=*` only behind a proxy that validates the host).
- **Cross-site requests** that change state (`POST/PUT/PATCH/DELETE` with a foreign `Origin` or
  `Sec-Fetch-Site: cross-site`) get 403, including multipart uploads and body-less posts.
- The OpenAlgo API key can place orders, so it never travels to another machine by accident: moving the OpenAlgo
  host to **any address off this machine** (public or LAN) requires re-entering the API key, and a remote OpenAlgo
  must use `https://` - plain `http://` to another machine is refused unless `allow_insecure_remote_http` is sent
  explicitly (trusted private network only). Moving between local ports keeps the key. (Integration note: the
  stricter flagfix1 policy was kept over flagfix2's "private network is fine" rule.)
- Order-capable signal routes (promotion to paper, resume of a paper deployment, kill switch, order cancel) also
  need the `X-Historify-Token` header (`instance/signals_token`, mode 0600; only the Signals page carries it). Paper
  orders are only ever sent while OpenAlgo reports Analyzer (sandbox) mode - re-checked before every order.

## Authentication (v2, P6b)
- Login is **required automatically** when TradeAlpha listens on a non-loopback address (`TRADEALPHA_HOST=0.0.0.0` or a
  LAN IP), and can be switched on for local use (`tradealpha auth enable`, or `TRADEALPHA_AUTH=1`).
  `TRADEALPHA_AUTH=0` turns it off even on a network bind - only do that behind a proxy that authenticates.
- Passwords: scrypt (n=2^14, r=8, p=1, per-user salt), minimum 10 characters; changing a password ends all of the
  user's sessions; 5 failed logins per client address → lock-out (60 s, doubling to 15 min).
- Session cookie: signed, HttpOnly, SameSite=Lax, 7 days; set `TRADEALPHA_SECURE_COOKIES=1` when served over HTTPS.
- API tokens (`Authorization: Bearer hst_...`): only their SHA-256 is stored; shown once; revocable.
- The first account can be created on the login page only by a visitor on this computer - requests forwarded by a
  reverse proxy (X-Forwarded-For / Forwarded / X-Real-IP) never count as local. The same rule protects `/metrics`.

## Live trading (P6b)
Real-money orders need, at the same time: a deployment promoted to `live` through the paper evidence gate and the
live gate (typed confirmation, not forceable), the global live switch (confirmation phrase, requires authentication
and a global exposure cap), complete per-deployment limits, the `X-Historify-Token` header on order-capable routes,
and OpenAlgo reporting **Analyzer mode OFF** right before every live order (paper orders require it ON). The first
session(s) after promotion are dry runs. The kill switch cancels everything at the broker. Every action is audited.

## If you expose TradeAlpha on a network
Enable authentication (automatic on a non-loopback bind), serve it over HTTPS (a reverse proxy with TLS, then
`TRADEALPHA_SECURE_COOKIES=1` and `TRADEALPHA_ALLOWED_HOSTS=<your host name>`), and prefer a VPN for remote access.

## Reporting a vulnerability
Please open a private security advisory on GitHub ("Security" tab -> "Report a vulnerability") rather than a public
issue. We aim to respond within 7 days.
