package com.alfakhamah.store;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/**
 * Decides what the WebView shell may do with a URL. Pure Java so it is covered by plain JUnit tests.
 *
 * - The storefront and the admin panel (/admin/) are both pages of our own server and load inside the app;
 *   the admin panel is protected by its own login.
 * - Any other website opens in the system browser.
 * - tel:/mailto:/sms:/geo: go to the matching system app; everything else (intent:, javascript:, file:,
 *   content:, data:, blob:, http on our own host, ...) is blocked.
 */
public final class NavigationPolicy {

    public enum Action { LOAD_IN_APP, OPEN_EXTERNALLY, BLOCK }

    private final String origin;

    /** @param origin normalised origin from {@link ServerOrigin#normalize} */
    public NavigationPolicy(String origin) {
        this.origin = origin;
    }

    public Action decide(String url) {
        if (url == null || url.trim().isEmpty()) return Action.BLOCK;
        URI uri;
        try {
            uri = new URI(url.trim()).normalize();
        } catch (URISyntaxException e) {
            return Action.BLOCK;
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        switch (scheme) {
            case "https":
            case "http":
                return decideWeb(uri, scheme);
            case "tel":
            case "mailto":
            case "sms":
            case "smsto":
            case "geo":
                return Action.OPEN_EXTERNALLY;
            default:
                return Action.BLOCK;
        }
    }

    private Action decideWeb(URI uri, String scheme) {
        String target = ServerOrigin.originOf(uri);
        if (target == null) return Action.BLOCK;
        if (target.equals(origin)) return Action.LOAD_IN_APP;
        // Our own host reached over another scheme (http instead of https) is a downgrade: never follow it.
        URI own = URI.create(origin);
        if (own.getHost() != null && own.getHost().equalsIgnoreCase(uri.getHost()) && !own.getScheme().equalsIgnoreCase(scheme)) {
            return Action.BLOCK;
        }
        return Action.OPEN_EXTERNALLY;
    }

    /**
     * True when the URL is the admin area of our own server. Used to hide the screen from screenshots and the
     * recents view while the admin panel is showing. Resolves what Chromium will really request: backslashes act
     * as slashes, "." / ".." segments collapse (also after percent-decoding), repeated slashes merge.
     */
    public boolean isAdminArea(String url) {
        if (decide(url) != Action.LOAD_IN_APP) return false;
        try {
            String path = cleanPath(new URI(url.trim()).getPath());
            return path.equals("/admin") || path.startsWith("/admin/") || path.equals("/admin.html") || path.startsWith("/api/admin/");
        } catch (URISyntaxException e) {
            return false;
        }
    }

    /** Only the admin export API may be downloaded from, and only on our own origin. */
    public boolean isDownloadAllowed(String url) {
        if (decide(url) != Action.LOAD_IN_APP) return false;
        try {
            return cleanPath(new URI(url.trim()).getPath()).startsWith("/api/admin/");
        } catch (URISyntaxException e) {
            return false;
        }
    }

    static String cleanPath(String rawDecodedPath) {
        String path = rawDecodedPath == null ? "" : rawDecodedPath.replace('\\', '/');
        java.util.ArrayDeque<String> segments = new java.util.ArrayDeque<>();
        for (String segment : path.split("/", -1)) {
            if (segment.equals("..")) {
                if (!segments.isEmpty()) segments.removeLast();
            } else if (!segment.isEmpty() && !segment.equals(".")) {
                segments.addLast(segment);
            }
        }
        boolean trailingSlash = path.endsWith("/") || path.endsWith("/.") || path.endsWith("/..");
        String joined = "/" + String.join("/", segments);
        return trailingSlash && !joined.endsWith("/") ? joined + "/" : joined;
    }
}
