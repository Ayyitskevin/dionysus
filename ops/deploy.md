# Dionysus Production Bring-Up

This is the chronological deployment path for test-mode launch.

## 1. Provision the host

```bash
sudo mkdir -p /opt/dionysus
sudo chown -R www-data:www-data /opt/dionysus
git clone git@github.com:Ayyitskevin/dionysus.git /opt/dionysus
cd /opt/dionysus
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Create Stripe products and prices

In Stripe test mode, create three recurring monthly prices:

- Restaurant Starter: `$49/mo`
- Restaurant Growth: `$149/mo`
- Photographer Studio: `$99/mo`

Copy each `price_...` ID into `/opt/dionysus/.env`.

## 3. Configure webhook

Stripe endpoint:

```text
https://<your-domain>/stripe/webhook
```

Events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Copy the `whsec_...` signing secret into `/opt/dionysus/.env`.

## 4. Write production env

Use `ops/env.example` as the template. Required before launch:

- unique `DIONYSUS_SECRET_KEY`
- public HTTPS `DIONYSUS_BASE_URL`
- `DIONYSUS_COOKIE_SECURE=true`
- Stripe secret key
- all three Stripe price IDs
- Stripe webhook secret
- Mise bridge token

## 5. Verify readiness

```bash
.venv/bin/python -m app.cli migrate
.venv/bin/python -m app.cli check-production
```

The readiness command must return zero before routing traffic.

## 6. Install service

```bash
sudo cp ops/dionysus.service /etc/systemd/system/dionysus.service
sudo cp ops/dionysus-worker.service /etc/systemd/system/dionysus-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now dionysus dionysus-worker
curl -s http://127.0.0.1:8450/healthz
curl -s http://127.0.0.1:8450/readiness
systemctl status dionysus-worker --no-pager
```

## 7. Test money path

1. Create a test account.
2. Open `/w/<slug>/billing`.
3. Start checkout with a Stripe test card.
4. Confirm Stripe sends `checkout.session.completed`.
5. Confirm local `subscriptions.status` becomes `active`.
