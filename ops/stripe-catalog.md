# Stripe Catalog

Created from Flow using the existing Mise live Stripe secret key.

These IDs are not secrets; they identify the live monthly prices Dionysus uses
for subscription checkout.

| Plan | Lookup key | Price ID |
|---|---|---|
| Restaurant Starter | `dionysus_restaurant_starter_monthly` | `price_1TlKNa0MQv8VEk7y0g1OUY7j` |
| Restaurant Growth | `dionysus_restaurant_growth_monthly` | `price_1TlKNv0MQv8VEk7ykXZSSRvW` |
| Photographer Studio | `dionysus_photographer_studio_monthly` | `price_1TlKNv0MQv8VEk7yKyQ1390P` |

Flow env draft:

```text
/home/kevin-lee/dionysus.env
```

It contains generated Dionysus secrets plus the reused Mise Stripe secret. It is
intentionally untracked and `0600`.

Still required before production readiness can pass:

- choose the public Dionysus URL
- create the Stripe webhook endpoint for `https://<domain>/stripe/webhook`
- copy that endpoint's `whsec_...` into the deployment env
- provision `/opt/dionysus` on Flow or another host with sudo
