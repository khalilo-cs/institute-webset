import java.net.URI

plugins {
    id("com.android.application")
}

// ---------------------------------------------------------------------------------------------------------
// Configuration comes from -P properties or environment variables. Nothing secret or environment-specific
// is stored in the repository.
//   SERVER_ORIGIN            https origin of the shared server, e.g. https://shop.<your-domain>  (no path)
//   ANDROID_KEYSTORE_FILE    path of the release keystore (created and kept by the owner)
//   ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, ANDROID_KEY_PASSWORD
// ---------------------------------------------------------------------------------------------------------
fun config(name: String): String =
    (providers.gradleProperty(name).orNull ?: System.getenv(name) ?: "").trim()

val serverOrigin = config("SERVER_ORIGIN").trimEnd('/')
val ksFile = config("ANDROID_KEYSTORE_FILE")
val ksPassword = config("ANDROID_KEYSTORE_PASSWORD")
val ksAlias = config("ANDROID_KEY_ALIAS")
val ksKeyPassword = config("ANDROID_KEY_PASSWORD")
val hasSigning = ksFile.isNotEmpty() && ksPassword.isNotEmpty() && ksAlias.isNotEmpty() && ksKeyPassword.isNotEmpty()

android {
    namespace = "com.alfakhamah.store"
    compileSdk = 37

    defaultConfig {
        minSdk = 29 // Android 10+: DownloadManager can save to Downloads without a storage permission
        targetSdk = 36
        applicationId = "com.alfakhamah.store"
        versionCode = 1
        versionName = "1.0.0"
        buildConfigField("String", "SERVER_ORIGIN", "\"$serverOrigin\"")
        // One app for customers and the admin: it opens the storefront; the admin panel is reached from the
        // storefront footer or the "لوحة الإدارة" launcher shortcut and still requires the admin login.
        buildConfigField("String", "START_PATH", "\"/\"")
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        if (hasSigning) {
            create("release") {
                storeFile = file(ksFile)
                storePassword = ksPassword
                keyAlias = ksAlias
                keyPassword = ksKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            // Test builds only: the server address can be typed in at first launch (see MainActivity).
            buildConfigField("boolean", "ALLOW_SERVER_OVERRIDE", "true")
        }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            buildConfigField("boolean", "ALLOW_SERVER_OVERRIDE", "false")
            if (hasSigning) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        warningsAsErrors = false
    }
}

dependencies {
    implementation("androidx.activity:activity:1.13.0")
    implementation("androidx.core:core:1.19.1")
    testImplementation("junit:junit:4.13.2")
}

// ---------------------------------------------------------------------------------------------------------
// Release guard: a release APK is only built when it can really work in production. It must point at a
// permanent https domain (not localhost, an IP address, a tunnel or a preview host) and be signed with the
// owner's own key. Otherwise the build stops with an explanation instead of producing a misleading APK.
// ---------------------------------------------------------------------------------------------------------
val verifyReleaseInputs by tasks.registering {
    group = "verification"
    description = "Fails unless SERVER_ORIGIN is a permanent https domain and signing credentials are provided."
    val origin = serverOrigin
    val signed = hasSigning
    val keystore = ksFile
    doLast {
        val problems = mutableListOf<String>()
        val badSuffixes = listOf(
            ".local", ".localhost", ".internal", ".lan", ".home", ".test", ".example", ".invalid",
            ".trycloudflare.com", ".ngrok.io", ".ngrok-free.app", ".ngrok-free.dev", ".ngrok.app", ".ngrok.dev",
            ".loca.lt", ".localtunnel.me", ".serveo.net", ".lhr.life", ".localhost.run", ".pinggy.io", ".pinggy.link",
            ".app.github.dev", ".github.dev", ".gitpod.io", ".stackblitz.io", ".webcontainer.io", ".replit.dev", ".repl.co",
        )
        if (origin.isEmpty()) {
            problems += "SERVER_ORIGIN is not set. Pass -PSERVER_ORIGIN=https://<your permanent domain>."
        } else {
            val uri = try { URI(origin) } catch (e: Exception) { null }
            val host = uri?.host?.lowercase().orEmpty()
            if (uri == null || uri.scheme != "https") problems += "SERVER_ORIGIN must start with https:// (got '$origin')."
            else if (host.isEmpty()) problems += "SERVER_ORIGIN has no host name (got '$origin')."
            else {
                if (!uri.rawPath.isNullOrEmpty() || uri.rawQuery != null || uri.rawFragment != null || uri.rawUserInfo != null)
                    problems += "SERVER_ORIGIN must be only scheme://host[:port], without path, query or credentials."
                if (!host.contains('.')) problems += "'$host' is not a public domain name."
                if (Regex("^[0-9.]+$").matches(host) || host.contains(':')) problems += "'$host' is an IP address; use a domain name."
                if (host == "localhost" || badSuffixes.any { host.endsWith(it) } || host.split('.').first() == "example" || host == "example.com" || host.endsWith(".example.com") || host == "example.org" || host.endsWith(".example.org"))
                    problems += "'$host' is a local, placeholder, tunnel or preview host, not a permanent production domain."
            }
        }
        if (!signed) {
            problems += "Release signing is not configured. Provide ANDROID_KEYSTORE_FILE, ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS and ANDROID_KEY_PASSWORD (your own keystore)."
        } else if (!file(keystore).isFile) {
            problems += "ANDROID_KEYSTORE_FILE does not exist: $keystore"
        }
        if (problems.isNotEmpty()) {
            throw GradleException(
                "Refusing to build a release APK:\n  - " + problems.joinToString("\n  - ") +
                    "\nSee ANDROID.md. For a test build use the debug variant (assembleDebug)."
            )
        }
    }
}

tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    dependsOn(verifyReleaseInputs)
}
