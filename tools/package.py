"""Build and package locally; never installs into the user's Revit add-in folders."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, DOTNET_CLI_HOME=str(root/'.build-runtime'), DOTNET_CLI_TELEMETRY_OPTOUT='1')
subprocess.run(['dotnet', 'build', str(root/'revit/Ac2Revit/Ac2Revit.csproj'), '-c', 'Release', '-v:q'], cwd=root, env=env, check=True)
folder = sys.argv[1] if len(sys.argv)>1 else 'Revit2026'
if not re.fullmatch(r'Revit2026(?:-v[1-9][0-9]*)?',folder):
    raise SystemExit('Unsupported package directory')
out = root/'dist'/folder
out.mkdir(parents=True, exist_ok=True)
shutil.copy2(root/'revit/Ac2Revit/bin/Release/net8.0-windows/Ac2Revit.dll', out/'Ac2Revit.dll')
assets=out/'assets';assets.mkdir(exist_ok=True)
for name in ('AC2R_RectangularColumn.rfa','AC2R_RectangularBeam.rfa','AC2R_StructuralColumn.rft','AC2R_Void.rft'):
    shutil.copy2(root/'revit/assets'/name,assets/name)
template = (root/'revit/Ac2Revit.addin.template').read_text(encoding='utf-8')
(out/'Ac2Revit.addin').write_text(template.replace('__ASSEMBLY_PATH__', escape(str(out/'Ac2Revit.dll'))), encoding='utf-8')
print('Package:', out)
