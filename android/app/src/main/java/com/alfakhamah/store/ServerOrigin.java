package com.alfakhamah.store;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/**
 * Validates and normalises the server origin ("https://host[:port]") the app talks to.
 * Pure Java (no Android classes) so it is covered by plain JUnit tests.
 */
public final class ServerOrigin {

    public enum Reason { EMPTY, MALFORMED, NOT_HTTPS, CREDENTIALS, HAS_PATH }

    public static final class InvalidOriginException extends Exception {
        public final Reason reason;

        InvalidOriginException(Reason reason) {
            super(reason.name());
            this.reason = reason;
        }
    }

    private ServerOrigin() {}

    /**
     * @param allowLocalCleartext true only for test (debug) builds: plain http is then accepted for
     *                            localhost / emulator / private-LAN hosts. Everything else must be https.
     * @return the origin without a trailing slash, default ports removed, host lower-cased
     */
    public static String normalize(String raw, boolean allowLocalCleartext) throws InvalidOriginException {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) throw new InvalidOriginException(Reason.EMPTY);
        if (!value.contains("://")) value = "https://" + value;

        URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException e) {
            throw new InvalidOriginException(Reason.MALFORMED);
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost();
        if (host == null || host.isEmpty()) throw new InvalidOriginException(Reason.MALFORMED);
        host = host.toLowerCase(Locale.ROOT);
        if (uri.getRawUserInfo() != null) throw new InvalidOriginException(Reason.CREDENTIALS);
        String path = uri.getRawPath();
        if ((path != null && !path.isEmpty() && !path.equals("/")) || uri.getRawQuery() != null || uri.getRawFragment() != null) {
            throw new InvalidOriginException(Reason.HAS_PATH);
        }
        int port = uri.getPort();
        if (scheme.equals("https")) {
            if (port == 443) port = -1;
        } else if (scheme.equals("http") && allowLocalCleartext && isLocalHost(host)) {
            if (port == 80) port = -1;
        } else {
            throw new InvalidOriginException(Reason.NOT_HTTPS);
        }
        return scheme + "://" + host + (port > 0 ? ":" + port : "");
    }

    /** "scheme://host[:port]" of an absolute URL with default ports removed, or null if it has none. */
    static String originOf(URI uri) {
        if (uri == null || uri.getScheme() == null || uri.getHost() == null) return null;
        String scheme = uri.getScheme().toLowerCase(Locale.ROOT);
        int port = uri.getPort();
        if ((scheme.equals("https") && port == 443) || (scheme.equals("http") && port == 80)) port = -1;
        return scheme + "://" + uri.getHost().toLowerCase(Locale.ROOT) + (port > 0 ? ":" + port : "");
    }

    /** Loopback, the Android-emulator host alias, *.local and RFC 1918 private ranges. */
    static boolean isLocalHost(String host) {
        if (host.equals("localhost") || host.equals("10.0.2.2") || host.equals("[::1]") || host.equals("::1") || host.endsWith(".local")) {
            return true;
        }
        String[] parts = host.split("\\.");
        if (parts.length != 4) return false;
        int[] octets = new int[4];
        for (int i = 0; i < 4; i++) {
            try {
                octets[i] = Integer.parseInt(parts[i]);
            } catch (NumberFormatException e) {
                return false;
            }
            if (octets[i] < 0 || octets[i] > 255) return false;
        }
        return octets[0] == 127
                || octets[0] == 10
                || (octets[0] == 192 && octets[1] == 168)
                || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31);
    }
}
