# Keep the Time Machine from reaching the rest of the tailnet

By default every device on a tailnet can open connections to every other one. The Time Machine serves a public
endpoint (Funnel), so if it were ever taken over, it shouldn't become a way into your laptop or servers. This tags the
Pi as an appliance: your own devices can still reach it (ssh, the options page), but it can't start connections to them.
Replies to connections you open still work, because Tailscale's filter tracks connections.

## 1. Policy (admin console > Access controls)

Merge these into your existing policy; don't replace rules you rely on. If your policy is still the default
"everyone can reach everything" rule, replace that rule with the `grants` below.

```jsonc
{
  "tagOwners": {
    "tag:appliance": ["autogroup:admin"],
  },

  "grants": [
    // your logged-in devices (laptop, phone, servers) can reach everything, including the Pi
    {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},
    // nothing lets tag:appliance start connections, so the Pi can't reach your other devices
  ],

  "nodeAttrs": [
    // Funnel for the Pi; once it's tagged, it no longer counts as your device
    {"target": ["tag:appliance"], "attr": ["funnel"]},
  ],

  "ssh": [
    // keep your existing ssh rules; Tailscale SSH to the Pi is not needed (plain ssh over the tailnet works)
  ],
}
```

If your policy uses `acls` instead of `grants`, the equivalent rule is
`{"action": "accept", "src": ["autogroup:member"], "dst": ["*:*"]}`, with no rule whose `src` is `tag:appliance`.

## 2. Tag the Pi

In the admin console: Machines > timemachine > ... > Edit ACL tags > `tag:appliance`. Or on the Pi:

```bash
sudo tailscale up --advertise-tags=tag:appliance
```

Tagged machines don't belong to a user and their keys don't expire, which suits an appliance.

## 3. Check

From the laptop, these still work: `ssh deadhead@timemachine` and http://timemachine:9090.
From the Pi, this should now time out (pick any other device on the tailnet):

```bash
curl -m 5 http://laptop-server/      # or: nc -vz -w 5 laptop-server 22
```

And the Funnel URL still answers from the internet (`tailscale funnel status` on the Pi).
