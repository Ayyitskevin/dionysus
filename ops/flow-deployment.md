# Flow Deployment Status

Current live URL:

```text
https://platekit.kleephotography.com
```

Verified public endpoints:

```text
https://platekit.kleephotography.com/healthz
https://platekit.kleephotography.com/readiness
```

Flow user-space checkout:

```text
/home/kevin-lee/ai-workspace/dionysus
```

Flow env files:

```text
/home/kevin-lee/ai-workspace/dionysus/.env
/home/kevin-lee/dionysus.env
```

Both are `0600` and contain secrets. Do not commit either file.

Cloudflare:

- User-owned tunnel: `platekit-dionysus`
- Tunnel ID: `49070d05-b263-40ae-a8e9-32a869c7376e`
- Hostname: `platekit.kleephotography.com`
- Local service: `http://127.0.0.1:8450`
- Stripe webhook URL: `https://platekit.kleephotography.com/stripe/webhook`
- Stripe webhook endpoint ID: `we_1TlKTB0MQv8VEk7yvNEY3SFG`

User systemd services on Flow:

```bash
systemctl --user status dionysus.service
systemctl --user status platekit-cloudflared.service
systemctl --user restart dionysus.service platekit-cloudflared.service
```

Important limitation:

These are user-level services because remote sudo is interactive-only. Reboot
persistence may require:

```bash
sudo loginctl enable-linger kevin-lee
```

The system-service version still belongs in `/opt/dionysus` later, but this
deployment is live and passes readiness without touching Mise's existing
system tunnel.
