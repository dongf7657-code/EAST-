@echo off
echo 正在安装依赖...
pip install -r requirements.txt
echo 正在打包...
pyinstaller --noconfirm --onedir --console --name "EastAIstudio" main.py
echo 打包完成！可执行文件位于 dist/EastAIstudio/EastAIstudio.exe
pause
