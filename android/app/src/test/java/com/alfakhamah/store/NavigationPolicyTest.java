package com.alfakhamah.store;

import static com.alfakhamah.store.NavigationPolicy.Action.BLOCK;
import static com.alfakhamah.store.NavigationPolicy.Action.LOAD_IN_APP;
import static com.alfakhamah.store.NavigationPolicy.Action.OPEN_EXTERNALLY;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class NavigationPolicyTest {
    private static final String ORIGIN = "https://shop.example.com";
    private final NavigationPolicy customer = new NavigationPolicy(ORIGIN, false);
    private final NavigationPolicy admin = new NavigationPolicy(ORIGIN, true);

    private static void check(NavigationPolicy policy, String url, NavigationPolicy.Action expected) {
        assertEquals(url, expected, policy.decide(url));
    }

    @Test
    public void customerStaysOnTheStore() {
        check(customer, ORIGIN + "/", LOAD_IN_APP);
        check(customer, ORIGIN + "/#shop", LOAD_IN_APP);
        check(customer, ORIGIN + ":443/", LOAD_IN_APP);
        check(customer, ORIGIN + "/assets/wavy-11.jpg", LOAD_IN_APP);
        check(customer, ORIGIN + "/uploads/abc.png", LOAD_IN_APP);
    }

    @Test
    public void customerCanNeverReachTheAdminArea() {
        check(customer, ORIGIN + "/admin", BLOCK);
        check(customer, ORIGIN + "/admin/", BLOCK);
        check(customer, ORIGIN + "/admin.html", BLOCK);
        check(customer, ORIGIN + "/api/admin/orders", BLOCK);
        check(customer, ORIGIN + "/x/%2e%2e/admin/", BLOCK);          // encoded dot segments
        check(customer, ORIGIN + "/x/../admin/", BLOCK);
        check(customer, ORIGIN + "/assets/..%2fadmin/", BLOCK);
        check(customer, ORIGIN + "/x\\..\\admin\\", BLOCK);            // backslashes act as slashes in Chromium
        check(customer, ORIGIN + "//admin/", BLOCK);
        check(customer, ORIGIN + "/admin/?next=/", BLOCK);
    }

    @Test
    public void adminStaysInsideTheAdminArea() {
        check(admin, ORIGIN + "/admin/", LOAD_IN_APP);
        check(admin, ORIGIN + "/admin", LOAD_IN_APP);
        check(admin, ORIGIN + "/api/admin/export/orders.csv", LOAD_IN_APP);
        check(admin, ORIGIN + "/assets/wavy-11.jpg", LOAD_IN_APP);
        check(admin, ORIGIN + "/uploads/abc.png", LOAD_IN_APP);
        // the public storefront opens in the browser, not inside the admin app
        check(admin, ORIGIN + "/", OPEN_EXTERNALLY);
        check(admin, ORIGIN + "/#shop", OPEN_EXTERNALLY);
        check(admin, ORIGIN + "/admin/../", OPEN_EXTERNALLY);
        check(admin, ORIGIN + "/x/%2e%2e/", OPEN_EXTERNALLY);
    }

    @Test
    public void otherWebsitesOpenInTheSystemBrowser() {
        for (NavigationPolicy p : new NavigationPolicy[]{customer, admin}) {
            check(p, "https://maps.app.goo.gl/kn2kxuPujQT9raLG7?g_st=awb", OPEN_EXTERNALLY);
            check(p, "https://wa.me/966500000000?text=hi", OPEN_EXTERNALLY);
            check(p, "https://shop.example.com.evil.com/", OPEN_EXTERNALLY);
            check(p, "https://evil.com/https://shop.example.com/", OPEN_EXTERNALLY);
            check(p, "https://shop.example.com:8443/", OPEN_EXTERNALLY);
            check(p, "https://sub.shop.example.com/", OPEN_EXTERNALLY);
        }
    }

    @Test
    public void downgradeToCleartextOnOurHostIsBlocked() {
        check(customer, "http://shop.example.com/", BLOCK);
        check(admin, "http://shop.example.com/admin/", BLOCK);
        check(customer, "http://other.example.org/", OPEN_EXTERNALLY);
    }

    @Test
    public void systemSchemesAreForwardedAndEverythingElseIsBlocked() {
        for (NavigationPolicy p : new NavigationPolicy[]{customer, admin}) {
            check(p, "tel:+966576486491", OPEN_EXTERNALLY);
            check(p, "mailto:a@b.c", OPEN_EXTERNALLY);
            check(p, "sms:+966576486491", OPEN_EXTERNALLY);
            check(p, "geo:21.5,39.2", OPEN_EXTERNALLY);
            check(p, "intent://scan/#Intent;scheme=zxing;end", BLOCK);
            check(p, "javascript:alert(1)", BLOCK);
            check(p, "file:///data/data/com.alfakhamah.store/shared_prefs/x.xml", BLOCK);
            check(p, "content://com.android.contacts/contacts", BLOCK);
            check(p, "data:text/html,<script>alert(1)</script>", BLOCK);
            check(p, "blob:https://shop.example.com/1234", BLOCK);
            check(p, "about:blank", BLOCK);
            check(p, "market://details?id=x", BLOCK);
            check(p, "android-app://com.example", BLOCK);
            check(p, "", BLOCK);
            check(p, null, BLOCK);
            check(p, "https://", BLOCK);
            check(p, "not a url", BLOCK);
        }
    }

    @Test
    public void onlyAdminMayDownloadAndOnlyFromItsApi() {
        assertTrue(admin.isDownloadAllowed(ORIGIN + "/api/admin/export/orders.csv"));
        assertTrue(admin.isDownloadAllowed(ORIGIN + ":443/api/admin/export/orders.csv"));
        assertFalse(admin.isDownloadAllowed(ORIGIN + "/uploads/a.png"));
        assertFalse(admin.isDownloadAllowed("https://evil.com/api/admin/export/orders.csv"));
        assertFalse(admin.isDownloadAllowed(ORIGIN + "/x/%2e%2e/api/admin/export/orders.csv/../../../../"));
        assertFalse(customer.isDownloadAllowed(ORIGIN + "/api/admin/export/orders.csv"));
        assertFalse(admin.isDownloadAllowed("javascript:alert(1)"));
    }

    @Test
    public void cleanPathResolvesDotSegments() {
        assertEquals("/", NavigationPolicy.cleanPath(null));
        assertEquals("/", NavigationPolicy.cleanPath(""));
        assertEquals("/a/b", NavigationPolicy.cleanPath("/a//b"));
        assertEquals("/b", NavigationPolicy.cleanPath("/a/../b"));
        assertEquals("/", NavigationPolicy.cleanPath("/../../.."));
        assertEquals("/a/", NavigationPolicy.cleanPath("/a/b/.."));
        assertEquals("/a/b", NavigationPolicy.cleanPath("\\a\\b"));
    }
}
