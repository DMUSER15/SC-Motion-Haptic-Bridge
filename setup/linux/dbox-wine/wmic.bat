@echo off
rem Workaround for Wine: its wmic has no "datafile" alias, so HaptiSync's Bonjour version check
rem (compare_bonjour_version.bat) can never read mDNSResponder.exe's version.
rem This shim answers ONLY that one query (datafile + mDNSResponder.exe) with the version of the
rem Bonjour package bundled in the HaptiSync installer (3.1.0.1). Everything else goes to the real wmic.
rem It does nothing unless C:\wmicshim is on the PATH (via WINEPATH for a single command).
setlocal enabledelayedexpansion
set "a=%*"
if "!a:mDNSResponder.exe=!"=="!a!" goto real
if "!a:datafile=!"=="!a!" goto real
echo.
echo Version=3.1.0.1
exit /b 0
:real
if exist "%SystemRoot%\system32\wbem\wmic.exe" goto realwbem
if exist "%SystemRoot%\system32\wmic.exe" goto realsys
echo wmic shim: could not find the real wmic.exe 1>&2
exit /b 1
:realwbem
"%SystemRoot%\system32\wbem\wmic.exe" %*
exit /b %errorlevel%
:realsys
"%SystemRoot%\system32\wmic.exe" %*
exit /b %errorlevel%
