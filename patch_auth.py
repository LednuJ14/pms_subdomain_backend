import os
import re

routes_dir = r"c:\Users\Lenovo\OneDrive\Desktop\JACS\sub-domain\backend\routes"

# Patterns to match various owner_id checks
patterns = [
    (r"if int\(property_obj\.owner_id\) != int\(current_user_id\):", r"if int(property_obj.owner_id) != int(current_user_id) and not is_super_admin(current_user_id):"),
    (r"if property_obj\.owner_id != current_user_id:", r"if property_obj.owner_id != current_user_id and not is_super_admin(current_user_id):"),
    (r"if property_obj\.owner_id != user\.id:", r"if property_obj.owner_id != user.id and not is_super_admin(user.id):"),
    (r"if property_obj\.owner_id != current_user\.id:", r"if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):"),
    (r"if not property_obj or property_obj\.owner_id != current_user\.id:", r"if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):"),
    (r"if property_obj and property_obj\.owner_id != current_user\.id:", r"if property_obj and property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):"),
]

import_statement = """from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'
"""

for filename in os.listdir(routes_dir):
    if not filename.endswith('.py'): continue
    filepath = os.path.join(routes_dir, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    modified = False
    
    for pattern, replacement in patterns:
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
            modified = True
            
    if modified:
        # Add the import and helper function if not already there
        if "def is_super_admin" not in content:
            # Find a good place to insert it (after imports)
            # Just insert it after Blueprint creation
            bp_match = re.search(r"(\w+_bp\s*=\s*Blueprint\([^)]+\))", content)
            if bp_match:
                content = content.replace(bp_match.group(1), bp_match.group(1) + "\n\n" + import_statement)
            else:
                pass
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Patched {filename}")
