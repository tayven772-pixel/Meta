# NexBoost for Android

NexBoost is an original, no-root Android gaming companion with a dark interface and green accents.

## Build an APK with GitHub Actions
1. Open the repository's **Actions** tab.
2. Choose **Build NexBoost APK** and run the workflow, or push a change under `NexBoostAndroid/`.
3. When the run finishes successfully, open the run and download the **NexBoost-debug-apk** artifact ZIP.
4. Extract `app-debug.apk` and install it on your Android device. Android may ask you to allow installs from that source.

## Honest capability notes
- Memory usage, battery percentage, Android release, and device model are read from Android APIs.
- Checklist state is stored locally on the device.
- Game profiles are advice only; they do not edit Minecraft or Roblox settings.
- No root, no fake FPS counters, no RAM-cleaning claims, and no guaranteed performance improvements.
- A debug APK is for testing. A release APK should be signed before public distribution.

## Project
- Application ID: `app.nexboost`
- Minimum Android version: Android 8.0 (API 26)
- Target SDK: 35
