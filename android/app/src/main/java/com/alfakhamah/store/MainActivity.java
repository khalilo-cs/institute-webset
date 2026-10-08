package com.alfakhamah.store;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JsResult;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.EdgeToEdge;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.SystemBarStyle;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

/**
 * One WebView shell shared by both apps (product flavors):
 * - customer: opens the storefront at "/"
 * - admin: opens the management panel at "/admin/" (the panel's own login screen; nothing is stored here)
 *
 * The server origin comes from the build (BuildConfig.SERVER_ORIGIN). Only test (debug) builds may
 * be pointed at another address at runtime.
 */
public class MainActivity extends ComponentActivity {

    private static final String PREFS = "server";
    private static final String PREF_ORIGIN = "origin";
    private static final boolean IS_ADMIN = "admin".equals(BuildConfig.APP_KIND);

    private WebView webView;
    private ProgressBar progress;
    private View errorView;
    private TextView errorTitle;
    private TextView errorMessage;
    private Button retryButton;
    private Button changeServerButton;
    private TextView testBanner;

    private String origin;
    private NavigationPolicy policy;
    private boolean pageFailed;
    private Bundle restoredState;

    private ValueCallback<Uri[]> fileCallback;
    private ActivityResultLauncher<Intent> fileChooserLauncher;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        EdgeToEdge.enable(this,
                SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT),
                SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT));
        super.onCreate(savedInstanceState);
        if (IS_ADMIN) {
            // The admin panel shows orders and customer phone numbers: keep it out of screenshots and the recents view.
            getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        }
        setContentView(R.layout.activity_main);
        restoredState = savedInstanceState;

        View root = findViewById(R.id.root);
        ViewCompat.setOnApplyWindowInsetsListener(root, (v, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.displayCutout() | WindowInsetsCompat.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return WindowInsetsCompat.CONSUMED;
        });

        webView = findViewById(R.id.web_view);
        progress = findViewById(R.id.progress);
        errorView = findViewById(R.id.error_view);
        errorTitle = findViewById(R.id.error_title);
        errorMessage = findViewById(R.id.error_message);
        retryButton = findViewById(R.id.retry_button);
        changeServerButton = findViewById(R.id.change_server_button);
        testBanner = findViewById(R.id.test_banner);

        retryButton.setOnClickListener(v -> retry());
        if (BuildConfig.ALLOW_SERVER_OVERRIDE) {
            changeServerButton.setVisibility(View.VISIBLE);
            changeServerButton.setOnClickListener(v -> promptForServer(null));
            testBanner.setOnClickListener(v -> promptForServer(null));
        }

        fileChooserLauncher = registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), result -> {
            if (fileCallback != null) {
                fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData()));
                fileCallback = null;
            }
        });

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (errorView.getVisibility() != View.VISIBLE && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        configureWebView();
        applyServer(resolveOrigin());
    }

    // ---------------------------------------------------------------------------------------- server origin

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Returns the origin to use, or null when none is configured (or the saved one is invalid). */
    private String resolveOrigin() {
        String candidate = BuildConfig.SERVER_ORIGIN;
        if (BuildConfig.ALLOW_SERVER_OVERRIDE) {
            String saved = prefs().getString(PREF_ORIGIN, "");
            if (saved != null && !saved.isEmpty()) candidate = saved;
        }
        try {
            return ServerOrigin.normalize(candidate, BuildConfig.ALLOW_SERVER_OVERRIDE);
        } catch (ServerOrigin.InvalidOriginException e) {
            return null;
        }
    }

    private void applyServer(String newOrigin) {
        origin = newOrigin;
        if (origin == null) {
            policy = null;
            if (BuildConfig.ALLOW_SERVER_OVERRIDE) {
                showError(getString(R.string.err_no_server_title), getString(R.string.err_no_server_message_debug), false);
                promptForServer(null);
            } else {
                showError(getString(R.string.err_not_configured_title), getString(R.string.err_not_configured_message), false);
            }
            return;
        }
        policy = new NavigationPolicy(origin, IS_ADMIN);
        if (BuildConfig.ALLOW_SERVER_OVERRIDE) {
            testBanner.setVisibility(View.VISIBLE);
            testBanner.setText(getString(R.string.test_banner, origin));
        }
        if (restoredState != null && webView.restoreState(restoredState) != null) {
            restoredState = null;
            return;
        }
        restoredState = null;
        webView.loadUrl(origin + BuildConfig.START_PATH);
    }

    private void promptForServer(String errorText) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setTextDirection(View.TEXT_DIRECTION_LTR);
        input.setHint(R.string.server_prompt_hint);
        input.setSingleLine(true);
        if (origin != null) input.setText(origin);
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(R.string.server_prompt_title)
                .setMessage(errorText == null ? getString(R.string.server_prompt_message) : errorText)
                .setView(input)
                .setPositiveButton(R.string.server_prompt_save, null)
                .setNegativeButton(R.string.cancel, null)
                .create();
        dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                String normalized = ServerOrigin.normalize(input.getText().toString(), true);
                prefs().edit().putString(PREF_ORIGIN, normalized).apply();
                dialog.dismiss();
                hideError();
                applyServer(normalized);
            } catch (ServerOrigin.InvalidOriginException e) {
                int message;
                switch (e.reason) {
                    case NOT_HTTPS: message = R.string.err_origin_not_https; break;
                    case HAS_PATH: message = R.string.err_origin_has_path; break;
                    case CREDENTIALS: message = R.string.err_origin_credentials; break;
                    default: message = R.string.err_origin_malformed;
                }
                input.setError(getString(message));
            }
        });
    }

    // ---------------------------------------------------------------------------------------- WebView

    @SuppressLint("SetJavaScriptEnabled") // the storefront and the admin panel are JavaScript applications
    private void configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        webView.setBackgroundColor(getColor(R.color.app_background));
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true); // the cart and favourites live in localStorage
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setGeolocationEnabled(false);
        s.setSupportMultipleWindows(true); // window.open() is routed through NavigationPolicy (wa.me, maps, ...)
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setSafeBrowsingEnabled(true);
        // The pages skip their service worker when they see this marker: the shell handles offline natively.
        s.setUserAgentString(s.getUserAgentString() + " AlfakhamahApp/" + BuildConfig.VERSION_NAME + " (" + BuildConfig.APP_KIND + ")");

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, false);

        webView.setWebViewClient(new ShellClient());
        webView.setWebChromeClient(new ShellChrome());
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> startDownload(url, userAgent, contentDisposition, mimeType));
    }

    private void retry() {
        hideError();
        if (origin == null) {
            applyServer(resolveOrigin());
        } else if (webView.getUrl() == null) {
            webView.loadUrl(origin + BuildConfig.START_PATH);
        } else {
            webView.reload();
        }
    }

    /** Applies NavigationPolicy; returns true when the WebView must NOT load the URL itself. */
    private boolean routeUrl(String url) {
        if (policy == null) return true;
        switch (policy.decide(url)) {
            case LOAD_IN_APP:
                return false;
            case OPEN_EXTERNALLY:
                openExternally(url);
                return true;
            default:
                Toast.makeText(this, R.string.link_blocked, Toast.LENGTH_SHORT).show();
                return true;
        }
    }

    private void openExternally(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, R.string.no_app_for_link, Toast.LENGTH_SHORT).show();
        }
    }

    private void startDownload(String url, String userAgent, String contentDisposition, String mimeType) {
        if (policy == null || !policy.isDownloadAllowed(url)) {
            Toast.makeText(this, R.string.link_blocked, Toast.LENGTH_SHORT).show();
            return;
        }
        String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
        DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
        String cookie = CookieManager.getInstance().getCookie(url);
        if (cookie != null) request.addRequestHeader("Cookie", cookie);
        request.addRequestHeader("User-Agent", userAgent);
        request.setTitle(name);
        if (mimeType != null && !mimeType.isEmpty()) request.setMimeType(mimeType);
        request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
        request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
        DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
        try {
            manager.enqueue(request);
            Toast.makeText(this, R.string.download_started, Toast.LENGTH_SHORT).show();
        } catch (RuntimeException e) {
            Toast.makeText(this, R.string.download_failed, Toast.LENGTH_LONG).show();
        }
    }

    // ---------------------------------------------------------------------------------------- error screen

    private void showError(String title, String message, boolean canRetry) {
        errorTitle.setText(title);
        errorMessage.setText(message);
        retryButton.setVisibility(canRetry ? View.VISIBLE : View.GONE);
        errorView.setVisibility(View.VISIBLE);
        webView.setVisibility(View.INVISIBLE);
        progress.setVisibility(View.GONE);
    }

    private void hideError() {
        pageFailed = false;
        errorView.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
    }

    private final class ShellClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return routeUrl(request.getUrl().toString());
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            pageFailed = false;
            progress.setVisibility(View.VISIBLE);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            progress.setVisibility(View.GONE);
            if (!pageFailed) {
                errorView.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
            }
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (!request.isForMainFrame()) return;
            pageFailed = true;
            showError(getString(R.string.err_offline_title), getString(R.string.err_offline_message), true);
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (request.isForMainFrame() && response.getStatusCode() >= 500) {
                pageFailed = true;
                showError(getString(R.string.err_server_title), getString(R.string.err_server_message), true);
            }
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel(); // never continue past a certificate problem
            pageFailed = true;
            showError(getString(R.string.err_ssl_title), getString(R.string.err_ssl_message), true);
        }

        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            pageFailed = true;
            showError(getString(R.string.err_render_title), getString(R.string.err_render_message), true);
            return true; // handled: the app must not crash when the WebView process is killed
        }
    }

    private final class ShellChrome extends WebChromeClient {
        @Override
        public void onProgressChanged(WebView view, int newProgress) {
            progress.setIndeterminate(false);
            progress.setProgress(newProgress);
            progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            try {
                fileChooserLauncher.launch(params.createIntent());
            } catch (ActivityNotFoundException e) {
                fileCallback = null;
                Toast.makeText(MainActivity.this, R.string.no_file_picker, Toast.LENGTH_LONG).show();
                return false;
            }
            return true;
        }

        @Override
        public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
            if (!isUserGesture) return false; // no pop-ups without a tap
            WebView popup = new WebView(MainActivity.this);
            popup.setWebViewClient(new WebViewClient() {
                private boolean handled;

                private void handleOnce(String url) {
                    if (handled) return;
                    handled = true;
                    routeUrl(url);
                    mainHandler.post(popup::destroy);
                }

                @Override
                public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                    handleOnce(request.getUrl().toString());
                    return true;
                }

                @Override
                public void onPageStarted(WebView v, String url, Bitmap favicon) {
                    handleOnce(url);
                }
            });
            ((WebView.WebViewTransport) resultMsg.obj).setWebView(popup);
            resultMsg.sendToTarget();
            return true;
        }

        // JavaScript dialogs are not shown by a WebView unless the app implements them; the admin panel
        // relies on confirm() before deleting products and categories.
        @Override
        public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
            if (isFinishing()) {
                result.cancel();
                return true;
            }
            new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setPositiveButton(R.string.ok, (d, w) -> result.confirm())
                    .setOnCancelListener(d -> result.cancel())
                    .show();
            return true;
        }

        @Override
        public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
            if (isFinishing()) {
                result.cancel();
                return true;
            }
            new AlertDialog.Builder(MainActivity.this)
                    .setMessage(message)
                    .setPositiveButton(R.string.ok, (d, w) -> result.confirm())
                    .setNegativeButton(R.string.cancel, (d, w) -> result.cancel())
                    .setOnCancelListener(d -> result.cancel())
                    .show();
            return true;
        }
    }

    // ---------------------------------------------------------------------------------------- lifecycle

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (webView != null && webView.getUrl() != null) webView.saveState(outState);
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush();
        webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (fileCallback != null) {
            fileCallback.onReceiveValue(null);
            fileCallback = null;
        }
        mainHandler.removeCallbacksAndMessages(null);
        ViewGroup parent = (ViewGroup) webView.getParent();
        if (parent != null) parent.removeView(webView);
        webView.destroy();
        super.onDestroy();
    }
}
