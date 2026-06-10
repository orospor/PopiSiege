# YouTube Script: "I Took Down a WordPress Site with 167 Bytes"

**Format:** Tutorial / Security Research  
**Length:** ~12–15 minutes  
**Tone:** Conversational, technical but accessible  
**Audience:** WordPress developers, security researchers, site owners  

---

## HOOK (0:00 – 0:45)

**[SCREEN: Terminal with popisiege running. Status shows DEGRADED → DOWN]**

What you're looking at is a WordPress site going offline.

Not because of some complex exploit. Not because of a zero-day. Not because I had any credentials.

I did it with a 167-byte form submission. And a $5 VPS.

The site had Cloudflare in front of it. It had Wordfence installed. It even had reCAPTCHA on the form.

None of it mattered.

**[SCREEN: cf-cache-status: DYNAMIC header highlighted]**

This one response header is why.

Stick around — I'm going to show you exactly how this works, why Cloudflare doesn't protect you from it, and most importantly — how to fix it in about 60 seconds.

---

## WHO THIS IS FOR (0:45 – 1:15)

**[SCREEN: WordPress admin dashboard with CF7 plugin]**

If you run a WordPress site with Contact Form 7 — that's over 5 million sites — this affects you.

If you're behind Cloudflare and you think that means you're safe from application-layer attacks — this video will change that assumption.

And if you're a security researcher who wants to understand Layer 7 DoS mechanics — we're going deep today.

This is my own site. I'm testing against infrastructure I own. Don't be stupid — only test on systems you have written permission to test.

---

## THE SETUP — HOW WORDPRESS + CLOUDFLARE WORKS (1:15 – 3:00)

**[ANIMATION: Browser → Cloudflare → Origin server flow]**

Let me explain how this is supposed to work.

You visit a WordPress site. Your request hits Cloudflare first. Cloudflare checks — do I have a cached version of this page?

If yes: it serves it directly from the edge. Your origin server never even wakes up. This is why Cloudflare can protect a $5 VPS from a million requests — it absorbs the load at the CDN layer.

**[ANIMATION: Cache HIT vs MISS]**

If no — cache miss — Cloudflare forwards the request to your origin. Your PHP server wakes up, runs WordPress, generates the page, and sends it back.

This works beautifully for GET requests. Blog posts, homepages, product pages — all cached.

**[ANIMATION: POST request going straight through]**

But POST requests? Cloudflare never caches them.

And this is where Contact Form 7 becomes a problem.

---

## THE VULNERABILITY (3:00 – 5:30)

**[SCREEN: CF7 form on a live WordPress site]**

When you submit a Contact Form 7 form, it doesn't hit the traditional WordPress form handler. Since CF7 version 5, it uses the WordPress REST API:

**[SCREEN: Terminal showing the endpoint URL]**

```
POST /wp-json/contact-form-7/v1/contact-forms/50/feedback
```

Watch what happens when I hit this endpoint.

**[SCREEN: curl command running, response headers shown]**

```
HTTP/2 200
cf-cache-status: DYNAMIC
```

`cf-cache-status: DYNAMIC`.

This means Cloudflare looked at this request and said: "I'm not caching this. I'm forwarding it directly to origin. Every single time."

**[SCREEN: Highlight cf-cache-status value]**

That's not a bug. That's correct behavior for a form submission. You wouldn't want cached form responses — every submission is unique.

But here's the problem.

**[ANIMATION: PHP worker pool diagram]**

Your origin server has a PHP worker pool. Think of it like a restaurant kitchen with a fixed number of chefs. Each incoming request that needs PHP — that's a customer order. The chef picks it up, makes the dish — about 1.2 seconds for a CF7 submission — and then they're free for the next order.

At 19 customers at once on this server? All 19 chefs are busy. Order 20 has to wait. Order 21 waits. Orders 22 through 50 — the restaurant prints a sign saying "we're closed."

That sign looks like this to your visitors:

**[SCREEN: Cloudflare 522 error page]**

Error 522. Connection timed out.

And since all workers are shared — it's not just your contact form that's broken. It's your entire site. Homepage, checkout, everything.

---

## THE ATTACK — LIVE DEMO (5:30 – 8:00)

**[SCREEN: Terminal, popisiege starting up]**

Let me show you this happening in real time.

I've got a tool called PopiSiege — open source, link in description. It sends concurrent CF7 form submissions through rotating proxies and measures availability in real time.

The proxy rotation matters — without it, Cloudflare or Wordfence bans the attacking IP after a few requests. With 100+ different proxy IPs, each request looks like it's coming from a different person.

**[SCREEN: popisiege running, burst output updating in real time]**

Watch the "Avail" column.

```
[Burst    1] OK=19/19 | Avail=100.0% | Avg=1.21s | Status=STABLE
[Burst    2] OK=17/19 | Avail= 89.4% | Avg=2.34s | Status=STABLE
[Burst    3] OK=13/19 | Avail= 68.4% | Avg=4.12s | Status=DEGRADED
[Burst    4] OK= 8/19 | Avail= 42.1% | Avg=7.89s | Status=DEGRADED
[Burst    5] OK= 3/19 | Avg=11.2s    | Status=DOWN
```

**[SCREEN: Open browser tab showing the site returning 522]**

And if I open the site right now in a browser...

**[PAUSE — show 522 error loading]**

Error 522.

That's 19 concurrent requests from one $5 VPS taking down a live WordPress site with Cloudflare and Wordfence protection.

**[SCREEN: Response time graph going up]**

Notice the average response time climbing — 1.2 seconds, 2.3, 4, 7, 11. That's requests queuing up waiting for a free PHP worker. When they wait more than 25 seconds — they time out. Those are your legitimate users getting errors.

**[SCREEN: Highlight 167 bytes payload size]**

The payload? 167 bytes. A small form submission. For every 167 bytes I send, the server does thousands of bytes of work — bootstrapping WordPress, querying the database, running Wordfence, calling reCAPTCHA. And then serving an 18,000-byte error page when it blocks me.

That's a 113,000 to 1 amplification ratio.

---

## WHY RECAPTCHA DOESN'T HELP (8:00 – 9:00)

**[SCREEN: reCAPTCHA on contact form]**

"But my form has reCAPTCHA!"

I had that conversation. Here's why it doesn't matter for this attack.

**[ANIMATION: Timeline of PHP worker processing]**

When a CF7 form is submitted, the PHP worker picks it up immediately. Then it:
1. Bootstraps WordPress — time passes
2. Loads CF7 — time passes
3. Runs Wordfence scan — time passes
4. THEN calls the reCAPTCHA API to validate

By step 4, the worker has already been occupied for 0.8 seconds. The reCAPTCHA check fails — submission rejected — but the damage is done. The worker was busy. Multiply by 19 concurrent requests and you've exhausted the pool before a single reCAPTCHA even gets checked.

reCAPTCHA prevents successful form submissions. It doesn't prevent worker exhaustion.

---

## THE "FIX" THAT DIDN'T FIX IT (9:00 – 9:45)

**[SCREEN: WordPress REST API returning 403]**

Here's a mistake I made trying to fix this.

I blocked the CF7 REST API listing endpoint — the one that lets you discover form IDs. The `/wp-json/contact-form-7/v1/contact-forms` endpoint now returns 403.

**[SCREEN: curl to listing endpoint → 403]**

Looks fixed, right?

**[SCREEN: curl to feedback endpoint → 200]**

The feedback submission endpoint is a completely separate route. Blocking one doesn't touch the other.

And here's the thing — an attacker who already has your form ID doesn't need the listing endpoint at all. The form ID is visible in your page source, in the form's hidden fields.

**[SCREEN: Page source showing _wpcf7=50]**

Right there. `_wpcf7=50`. Form ID 50.

Blocking metadata enumeration is not a fix.

---

## THE ACTUAL FIX (9:45 – 11:00)

**[SCREEN: Cloudflare dashboard → Security → WAF → Rate Limiting Rules]**

The real fix takes 60 seconds and lives entirely at Cloudflare's edge.

Go to Cloudflare → Security → WAF → Rate Limiting Rules. Create a new rule.

**[SCREEN: Rule configuration being filled in]**

Field: URI Path contains `/wp-json/contact-form-7`  
AND  
Field: Request Method equals `POST`

Action: Block  
Rate: 5 requests per 10 seconds per IP

**[SCREEN: Save and deploy]**

That's it.

Why does this work when Cloudflare wasn't helping before? Because Cloudflare's default behavior is to forward POST requests to your origin. This rule tells it to COUNT those requests per IP and BLOCK at the edge before PHP ever runs.

5 per 10 seconds is generous — no legitimate user submits 5 contact forms in 10 seconds. A real user might submit one, wait for confirmation, maybe retry once. That's 2 in 10 seconds. You're safe.

An attacker sending 19 simultaneous requests? Blocked at request 6. PHP never wakes up. Zero origin cost.

**[SCREEN: Re-run popisiege after fix is applied]**

Watch what happens when I run the same attack after adding the rule.

```
[Burst    1] OK= 5/19 | Avail= 26.3% | Status=DEGRADED  ← first 5 slip through
[Burst    2] OK= 0/19 | Avail=  0.0% | Status=DOWN       ← all blocked at edge
[Burst    3] OK= 0/19 | Avail=  0.0% | Status=DOWN
```

The first burst lets through 5 before the rate limit kicks in — those are the allowed 5. Every subsequent request is blocked by Cloudflare, never touching PHP. Origin load: zero.

---

## WHAT THIS MEANS FOR YOU (11:00 – 12:00)

**[SCREEN: WordPress.org plugin stats — 5M+ active installs]**

Contact Form 7 has over 5 million active installations. If you're running it behind Cloudflare without a rate limiting rule on that endpoint, you're exposed to this right now.

The attack requires:
- Knowledge of your form ID — trivially discoverable from page source
- About 100 free proxies — available on GitHub
- A $5/month VPS
- 19 concurrent connections

That's not a sophisticated attacker. That's a mildly annoyed person with an afternoon free.

**[SCREEN: Cloudflare rate limiting rule interface]**

The fix is free, takes 60 seconds, and has zero impact on legitimate users.

If you manage WordPress sites — go add this rule right now. I'll leave the exact configuration in the description.

---

## OUTRO (12:00 – 12:45)

**[SCREEN: GitHub repo — PopiSiege]**

All the research tools we used are open source. PopiSiege is on GitHub — link in the description. It's for authorized security testing on infrastructure you own or have permission to test.

The white paper with full technical details, CVSS scoring, and responsible disclosure timeline is also in the repo.

We're submitting this to the CF7 team and Wordfence for responsible disclosure. If you're a security researcher and you've found similar issues, the WordPress HackerOne program is the right place.

Drop any questions in the comments. Like and subscribe if this was useful — we're documenting more WordPress security research as we go.

And if you're a site owner who just added that Cloudflare rule — you're welcome.

---

## DESCRIPTION (for YouTube)

**WordPress + CF7 + Cloudflare = DoS vulnerability. Here's how it works and how to fix it.**

Contact Form 7's REST API forces `cf-cache-status: DYNAMIC` on every POST request, bypassing Cloudflare's cache entirely. Concurrent submissions exhaust PHP worker pools, taking sites offline with payloads as small as 167 bytes.

**The fix (Cloudflare Rate Limiting Rule):**
```
URI Path contains /wp-json/contact-form-7
AND Request Method = POST
→ Block at 5 requests / 10 seconds / IP
```

**Links:**
- PopiSiege tool: https://github.com/orospor/PopiSiege
- CF7 Plugin: https://wordpress.org/plugins/contact-form-7/
- Cloudflare Rate Limiting docs: https://developers.cloudflare.com/waf/rate-limiting-rules/
- WordPress HackerOne: https://hackerone.com/wordpress

**Chapters:**
- 0:00 Hook — live attack demo
- 1:15 How WordPress + Cloudflare works
- 3:00 The vulnerability explained
- 5:30 Live demo
- 8:00 Why reCAPTCHA doesn't help
- 9:00 The fix that didn't fix it
- 9:45 The actual fix (60 seconds)
- 11:00 Impact and who's affected
- 12:00 Outro + tools

*For authorized security research only. Only test systems you own or have written permission to test.*

---

## THUMBNAIL IDEAS

**Option A:** Terminal showing `Status=DOWN` in red + text overlay "167 bytes took down this WordPress site"

**Option B:** Cloudflare logo with a crack through it + text "Cloudflare can't stop this"

**Option C:** Split screen — WordPress site loading normally vs 522 error — text "What Cloudflare doesn't protect you from"
