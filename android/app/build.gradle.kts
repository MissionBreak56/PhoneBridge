plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.phonebridge"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.phonebridge"
        minSdk = 24
        targetSdk = 34
        versionCode = 7
        versionName = "3.0.1"
    }

    // Assinatura de release: vem de variáveis de ambiente (GitHub Secrets no CI). Nada de senha ou keystore no código.
    val keystorePath: String? = System.getenv("ANDROID_KEYSTORE_PATH")
    signingConfigs {
        create("release") {
            if (!keystorePath.isNullOrEmpty()) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (!keystorePath.isNullOrEmpty()) signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.preference:preference-ktx:1.2.1")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0") // leitor de QR code
    testImplementation("junit:junit:4.13.2")
}
