@echo off
echo =====================================================================
echo  SafeMate Emergency - Android APK Local Build Script
echo =====================================================================

cd /d "%~dp0\frontend"
echo [1/3] Building Web Assets with Vite...
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Web build failed.
    pause
    exit /b %ERRORLEVEL%
)

echo [2/3] Syncing with Capacitor Android...
call npx cap sync android
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Capacitor sync failed.
    pause
    exit /b %ERRORLEVEL%
)

echo [3/3] Compiling Android APK with Gradle...
cd android
if exist gradlew.bat (
    call gradlew.bat assembleDebug
    if %ERRORLEVEL% EQU 0 (
        echo =====================================================================
        echo  SUCCESS: APK Built Successfully!
        echo  File: frontend\android\app\build\outputs\apk\debug\app-debug.apk
        echo =====================================================================
        copy /y "app\build\outputs\apk\debug\app-debug.apk" "..\..\SafeMate-Emergency.apk"
        echo Copied to root workspace: SafeMate-Emergency.apk
    ) else (
        echo [NOTE] Gradle build requires Android SDK installed on your system.
        echo You can also open the 'frontend\android' folder directly in Android Studio!
    )
) else (
    echo [ERROR] gradlew.bat not found.
)

pause

