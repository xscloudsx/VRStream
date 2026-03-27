@echo off
echo Installing requirements...
pip install -r requirements.txt
pip install pyinstaller

echo Building Live_Cdnd (Backend API)...
pyinstaller --onefile --console Live_Cdnd.py

echo Building VRStream Launcher (GUI)...
pyinstaller --onefile --windowed --name "VRStream" launcher.py

echo Organizing Deploy folder...
if not exist deploy mkdir deploy
copy dist\Live_Cdnd.exe deploy\
copy dist\VRStream.exe deploy\

echo --- BUILD COMPLETE ---
echo You can find VRStream.exe and Live_Cdnd.exe in the 'deploy' folder.
echo You only need to run VRStream.exe.
pause
