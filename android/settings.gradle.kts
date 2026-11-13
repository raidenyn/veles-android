// The Android build is independent of the repository root build (design D4). It includes the Rust
// build for the JNI and host-JVM libraries and nothing else outside android/.
pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "VelesAndroid"
include(":app")
// The Rust tasks are defined once, in rust/. Android consumes them through included-build task
// references so `rustJni`/`rustHostJni` run once per invocation and their outputs stay under the
// repository-root build/.
includeBuild("../rust")
