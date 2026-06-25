import json
import os

input_path = 'C:/Users/吴子瑞/WorkBuddy/2026-05-24-22-16-48/gulaoyuej/data/index.json'
output_path = 'C:/Users/吴子瑞/WorkBuddy/2026-05-24-22-16-48/gulaoyuej/data/index.json'
backup_path = 'C:/Users/吴子瑞/WorkBuddy/2026-05-24-22-16-48/gulaoyuej/data/index.json.bak'

if not os.path.exists(backup_path):
    import shutil
    shutil.copy2(input_path, backup_path)
    print(f'已备份原文件到 {backup_path}')

with open(input_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

for item in data:
    images = item.get('images', [])
    item['thumb'] = images[0] if images else ''
    del item['images']

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, separators=(',', ':'))

old_size = os.path.getsize(backup_path)
new_size = os.path.getsize(output_path)
print(f'原始大小: {old_size / 1024 / 1024:.2f} MB')
print(f'裁剪后: {new_size / 1024 / 1024:.2f} MB')
print(f'减少: {(1 - new_size / old_size) * 100:.1f}%')
