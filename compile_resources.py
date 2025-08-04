# Plik: compile_resources.py (WERSJA OSTATECZNA)

import os
import sys

print("Kompilowanie plików zasobów (.qrc) do formatu Python (.py)...")

# Krok 1: Znajdź ścieżkę do aktywnego interpretera Python
python_executable = sys.executable

# Krok 2: Zbuduj ścieżkę do katalogu ze skryptami (np. venv/Scripts)
scripts_dir = os.path.dirname(python_executable)

# Krok 3: Zbuduj pełną, absolutną ścieżkę do kompilatora pyrcc6
# Dodajemy .exe dla pewności w systemie Windows
compiler_path = os.path.join(scripts_dir, 'pyrcc6.exe')

# Sprawdzamy, czy kompilator istnieje w oczekiwanej lokalizacji
if not os.path.exists(compiler_path):
    # Próbujemy bez .exe dla systemów innych niż Windows
    compiler_path = os.path.join(scripts_dir, 'pyrcc6')
    if not os.path.exists(compiler_path):
        print(f"BŁĄD KRYTYCZNY: Nie znaleziono kompilatora 'pyrcc6' w {scripts_dir}")
        sys.exit(1) # Zakończ skrypt z błędem

# Krok 4: Zbuduj i wykonaj pełne polecenie z absolutną ścieżką
command = f'"{compiler_path}" assets.qrc -o resources_rc.py'
print(f"Wykonywanie polecenia: {command}")

exit_code = os.system(command)

if exit_code == 0:
    print("Kompilacja zakończona sukcesem. Utworzono plik resources_rc.py.")
else:
    print(f"BŁĄD: Kompilacja nie powiodła się. Kod wyjścia: {exit_code}")