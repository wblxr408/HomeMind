#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HomeMind 依赖安装脚本
帮助用户检查和安装项目依赖
"""
import subprocess
import sys
import os

REQUIRED_PACKAGES = {
    'flask': 'flask',
    'flask-cors': 'flask-cors',
    'flask-socketio': 'flask-socketio',
    'chromadb': 'chromadb',
    'sentence-transformers': 'sentence-transformers',
    'llama-cpp-python': 'llama-cpp-python',
    'openai': 'openai',
    'numpy': 'numpy',
    'scipy': 'scipy',
    'torch': 'torch',
}

OPTIONAL_PACKAGES = {
    'webbrowser': None,  # 标准库
    'sqlite3': None,    # 标准库
}

def check_package(package_name):
    try:
        if package_name == 'torch':
            __import__('torch')
        else:
            __import__(package_name.replace('-', '_'))
        return True
    except ImportError:
        return False

def get_missing_packages():
    missing = []
    for pkg, import_name in REQUIRED_PACKAGES.items():
        if not check_package(pkg):
            missing.append(pkg)
    return missing

def ensure_directories():
    dirs = ['data', 'models', 'data/knowledge', 'data/logs']
    for d in dirs:
        path = os.path.join(os.path.dirname(__file__), d)
        os.makedirs(path, exist_ok=True)
        print(f'  目录已创建/确认: {d}/')

def print_status():
    print('=' * 50)
    print('  HomeMind 依赖检查')
    print('=' * 50)
    
    print('\n[必需依赖]')
    all_ok = True
    for pkg in REQUIRED_PACKAGES:
        installed = check_package(pkg)
        status = '[OK]' if installed else '[MISSING]'
        print(f'  {pkg}: {status}')
        if not installed:
            all_ok = False
    
    print('\n[可选依赖]')
    for pkg in OPTIONAL_PACKAGES:
        if OPTIONAL_PACKAGES[pkg]:
            installed = check_package(pkg)
            status = '[OK]' if installed else '[MISSING]'
            print(f'  {pkg}: {status}')
    
    return all_ok

def install_packages(packages):
    print(f'\n正在安装 {len(packages)} 个缺失的包...')
    for pkg in packages:
        print(f'  安装 {pkg}...')
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg, '-q'])
            print(f'    [OK] {pkg} 安装成功')
        except subprocess.CalledProcessError as e:
            print(f'    [FAIL] {pkg} 安装失败: {e}')

def main():
    print_status()
    
    missing = get_missing_packages()
    
    if not missing:
        print('\n[完成] 所有必需依赖已安装!')
    else:
        print(f'\n[发现] {len(missing)} 个必需依赖未安装')
        response = input('\n是否自动安装? (y/n): ').strip().lower()
        if response == 'y':
            install_packages(missing)
            print_status()
        else:
            print('\n请手动安装缺失的包:')
            print('  pip install ' + ' '.join(missing))
    
    print('\n[目录检查]')
    ensure_directories()
    
    print('\n[下一步]')
    print('  运行 main.py 启动 HomeMind:')
    print('    python main.py')
    print('  或运行 web/server.py 启动 Web 服务:')
    print('    python web/server.py')

if __name__ == '__main__':
    main()
