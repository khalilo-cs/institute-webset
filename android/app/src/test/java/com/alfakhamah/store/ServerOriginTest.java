package com.alfakhamah.store;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public class ServerOriginTest {

    private static void rejects(String raw, boolean allowLocal, ServerOrigin.Reason expected) {
        try {
            String value = ServerOrigin.normalize(raw, allowLocal);
            fail("expected " + expected + " for '" + raw + "' but got " + value);
        } catch (ServerOrigin.InvalidOriginException e) {
            assertEquals(raw, expected, e.reason);
        }
    }

    @Test
    public void acceptsAndNormalisesHttpsOrigins() throws Exception {
        assertEquals("https://shop.example.com", ServerOrigin.normalize("https://shop.example.com", false));
        assertEquals("https://shop.example.com", ServerOrigin.normalize("  HTTPS://Shop.Example.COM/  ", false));
        assertEquals("https://shop.example.com", ServerOrigin.normalize("https://shop.example.com:443", false));
        assertEquals("https://shop.example.com:8443", ServerOrigin.normalize("https://shop.example.com:8443/", false));
        assertEquals("https://shop.example.com", ServerOrigin.normalize("shop.example.com", false)); // scheme defaults to https
    }

    @Test
    public void emptyAndMalformedInputIsRejected() {
        rejects(null, false, ServerOrigin.Reason.EMPTY);
        rejects("   ", false, ServerOrigin.Reason.EMPTY);
        rejects("https://", false, ServerOrigin.Reason.MALFORMED);
        rejects("https://exa mple.com", false, ServerOrigin.Reason.MALFORMED);
        rejects("https:///path", false, ServerOrigin.Reason.MALFORMED);
    }

    @Test
    public void releaseBuildsNeverAcceptCleartext() {
        rejects("http://shop.example.com", false, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://localhost:4173", false, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://10.0.2.2:4173", false, ServerOrigin.Reason.NOT_HTTPS);
        rejects("ftp://shop.example.com", false, ServerOrigin.Reason.NOT_HTTPS);
        rejects("javascript://shop.example.com", true, ServerOrigin.Reason.NOT_HTTPS);
    }

    @Test
    public void testBuildsAllowCleartextOnlyForLocalHosts() throws Exception {
        assertEquals("http://10.0.2.2:4173", ServerOrigin.normalize("http://10.0.2.2:4173", true));
        assertEquals("http://localhost:4173", ServerOrigin.normalize("http://localhost:4173", true));
        assertEquals("http://192.168.1.10:4173", ServerOrigin.normalize("http://192.168.1.10:4173", true));
        assertEquals("http://172.20.0.5", ServerOrigin.normalize("http://172.20.0.5:80", true));
        assertEquals("http://127.0.0.1:9000", ServerOrigin.normalize("http://127.0.0.1:9000", true));
        assertEquals("http://shop.local:4173", ServerOrigin.normalize("http://shop.local:4173", true));
        rejects("http://shop.example.com", true, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://8.8.8.8", true, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://172.32.0.1", true, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://192.169.1.1", true, ServerOrigin.Reason.NOT_HTTPS);
        rejects("http://10.0.2.2.evil.com", true, ServerOrigin.Reason.NOT_HTTPS);
    }

    @Test
    public void pathsQueriesAndCredentialsAreRejected() {
        rejects("https://shop.example.com/admin", false, ServerOrigin.Reason.HAS_PATH);
        rejects("https://shop.example.com/?a=1", false, ServerOrigin.Reason.HAS_PATH);
        rejects("https://shop.example.com/#x", false, ServerOrigin.Reason.HAS_PATH);
        rejects("https://admin:secret@shop.example.com", false, ServerOrigin.Reason.CREDENTIALS);
    }

    @Test
    public void localHostDetection() {
        assertTrue(ServerOrigin.isLocalHost("localhost"));
        assertTrue(ServerOrigin.isLocalHost("10.1.2.3"));
        assertTrue(ServerOrigin.isLocalHost("172.16.0.1"));
        assertTrue(ServerOrigin.isLocalHost("172.31.255.255"));
        assertFalse(ServerOrigin.isLocalHost("172.15.0.1"));
        assertFalse(ServerOrigin.isLocalHost("11.0.0.1"));
        assertFalse(ServerOrigin.isLocalHost("256.1.1.1"));
        assertFalse(ServerOrigin.isLocalHost("example.com"));
        assertFalse(ServerOrigin.isLocalHost("localhost.example.com"));
    }
}
