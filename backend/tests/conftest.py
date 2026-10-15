import importlib.util
import os


def load(name, folder):
    path = os.path.join(os.path.dirname(__file__), "..", folder, "handler.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
