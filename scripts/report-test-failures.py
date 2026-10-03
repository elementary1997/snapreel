"""Ошибки pytest видны в аннотациях CI даже без доступа к полному журналу."""

from pathlib import Path
import xml.etree.ElementTree as ET

path = Path('pytest-results.xml')
if path.exists():
    root = ET.parse(path).getroot()
    for case in root.iter('testcase'):
        for kind in ('failure', 'error'):
            failure = case.find(kind)
            if failure is not None:
                message = (failure.text or failure.get('message', ''))[-12000:]
                message = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                name = case.get('name', 'pytest').replace(',', '_').replace(':', '_')
                print(f'::error title={name}::{message}')
