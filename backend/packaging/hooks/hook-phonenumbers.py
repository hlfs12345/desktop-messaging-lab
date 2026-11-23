"""Metadados carregados dinamicamente; sem geocoder/carrier/timezone."""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("phonenumbers.data")
