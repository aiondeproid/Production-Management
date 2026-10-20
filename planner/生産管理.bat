@echo off
rem Program files (src, static, lib, python) are copied to this PC and run from there,
rem because opening many small files on the shared folder is very slow.
rem Data, config.json and output stay in this folder (passed as PLANNER_HOME).
set "PLANNER_HOME=%~dp0"
set "RUN=%LOCALAPPDATA%\SeizoKeikakuApp"
for %%d in (src static lib python) do call :sync %%d || goto :fail
if exist "%RUN%\python\python.exe" (
  "%RUN%\python\python.exe" "%RUN%\src\server.py" %*
) else (
  python "%RUN%\src\server.py" %*
)
if errorlevel 1 pause
exit /b

:sync
if not exist "%PLANNER_HOME%%1" exit /b 0
rem Only changed files are copied. Robocopy exit code 8 or higher means failure.
robocopy "%PLANNER_HOME%%1" "%RUN%\%1" /MIR /XD __pycache__ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP > nul
if errorlevel 8 exit /b 1
exit /b 0

:fail
echo Failed to copy program files to "%RUN%".
pause
exit /b 1
