"""
System zarzadzania kontem bankowym
Wariant: Zespol F

Zakres:
3. Uruchomienie i weryfikacja SUT
4. Dekorator logujacy awarie
5. Testy eksploracyjne (28 przypadkow)
6. Wstepne wskazniki jakosciowe
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import date
from functools import wraps
import threading
import time
import random
import logging
import math
import inspect
import ast
import tokenize
import io


# ============================================================
# KONFIGURACJA LOGOWANIA
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

LIMIT_DZIENNY_WYPLAT = Decimal("5000")
LIMIT_PRZELEWU = Decimal("1000000")


# ============================================================
# WYJATKI
# ============================================================

class KontoNieIstnieje(Exception):
    """Operacja dotyczy nieistniejacego konta."""


class ProcesorNiedostepny(Exception):
    """Zewnetrzny procesor platnosci jest niedostepny."""


class KontoZablokowane(Exception):
    """Operacja na zablokowanym koncie."""


# ============================================================
# DEKORATOR LOGUJACY AWARIE
# ============================================================

def loguj_awarie(func):
    """
    Loguje czas wykonania operacji oraz szczegoly wyjatku.
    Po zalogowaniu ponownie zglasza wyjatek.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()

        try:
            wynik = func(*args, **kwargs)
            return wynik
        except Exception as exc:
            czas = time.perf_counter() - start

            logging.error(
                "Awaria w funkcji '%s': %s - %s "
                "(czas do awarii: %.6f s)",
                func.__name__,
                type(exc).__name__,
                exc,
                czas
            )
            raise

    return wrapper


# ============================================================
# ZEWNĘTRZNY PROCESOR PLATNOSCI
# ============================================================

class ZewnetrznyProcesor:
    """
    Symulacja zewnetrznego procesora platnosci.
    Awaria jest losowa i zalezy od ustawionego prawdopodobienstwa.
    """

    def __init__(
        self,
        prawdopodobienstwo_awarii=0.3,
        opoznienie_s=0.0,
        generator=None
    ):
        if not 0 <= prawdopodobienstwo_awarii <= 1:
            raise ValueError(
                "Prawdopodobienstwo awarii musi nalezec do [0, 1]"
            )

        if opoznienie_s < 0:
            raise ValueError("Opoznienie nie moze byc ujemne")

        self.prawdopodobienstwo_awarii = prawdopodobienstwo_awarii
        self.opoznienie_s = opoznienie_s
        self._generator = generator or random.Random()
        self.licznik_wywolan = 0

    def wyslij_przelew(self, numer_konta_docelowego, kwota):
        self.licznik_wywolan += 1

        if self.opoznienie_s:
            time.sleep(self.opoznienie_s)

        if (
            self._generator.random()
            < self.prawdopodobienstwo_awarii
        ):
            raise ProcesorNiedostepny(
                "Zewnetrzny procesor platnosci nie odpowiada"
            )

        return {
            "status": "zaksiegowano",
            "numer_referencyjny": (
                f"REF{self.licznik_wywolan:06d}"
            )
        }


# ============================================================
# KONTO BANKOWE
# ============================================================

class Konto:
    def __init__(self, numer, wlasciciel, saldo_poczatkowe=0):
        self.numer = numer
        self.wlasciciel = wlasciciel
        self.saldo = Decimal(str(saldo_poczatkowe))
        self.historia = []
        self.status = "aktywne"

    def dodaj_wpis(self, typ, kwota, data_operacji=None):
        if data_operacji is None:
            data_operacji = date.today()

        self.historia.append(
            (data_operacji, typ, kwota)
        )


# ============================================================
# SYSTEM BANKOWY
# ============================================================

class BankSystem:
    def __init__(self):
        self.konta = {}
        self._lock = threading.RLock()

    @staticmethod
    def _numer_poprawny(numer):
        if (
            not isinstance(numer, str)
            or len(numer) != 10
            or not numer.isascii()
            or not numer.isdigit()
        ):
            return False

        cyfry = [int(c) for c in numer]
        suma_kontrolna = sum(cyfry[:9]) % 10

        return cyfry[9] == suma_kontrolna

    @staticmethod
    def _kwota_decimal(kwota):
        try:
            wynik = Decimal(str(kwota))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("Nieprawidlowa kwota") from None

        if not wynik.is_finite():
            raise ValueError("Kwota musi byc liczba skonczona")

        return wynik

    @staticmethod
    def _sprawdz_saldo_poczatkowe(saldo):
        saldo = BankSystem._kwota_decimal(saldo)

        if saldo < 0:
            raise ValueError(
                "Saldo poczatkowe nie moze byc ujemne"
            )

        return saldo

    def _pobierz_konto(self, numer):
        if numer not in self.konta:
            raise KontoNieIstnieje(
                f"Konto {numer} nie istnieje"
            )

        return self.konta[numer]

    @loguj_awarie
    def utworz_konto(
        self,
        numer,
        wlasciciel,
        saldo_poczatkowe=0
    ):
        with self._lock:
            if not self._numer_poprawny(numer):
                raise ValueError(
                    "Nieprawidlowy numer konta"
                )

            if numer in self.konta:
                raise ValueError(
                    "Konto o takim numerze juz istnieje"
                )

            if not isinstance(wlasciciel, str) or not wlasciciel.strip():
                raise ValueError(
                    "Wlasciciel musi miec niepuste imie lub nazwe"
                )

            saldo = self._sprawdz_saldo_poczatkowe(
                saldo_poczatkowe
            )

            konto = Konto(numer, wlasciciel.strip(), saldo)
            self.konta[numer] = konto

            return konto

    def zablokuj_konto(self, numer):
        with self._lock:
            konto = self._pobierz_konto(numer)
            konto.status = "zablokowane"

    def odblokuj_konto(self, numer):
        with self._lock:
            konto = self._pobierz_konto(numer)
            konto.status = "aktywne"

    def _suma_dzisiejszych_wyplat(self, konto):
        dzis = date.today()
        suma = Decimal("0")

        for data_operacji, typ, kwota in konto.historia:
            if (
                data_operacji == dzis
                and typ in (
                    "wyplata",
                    "przelew_wychodzacy",
                    "przelew_zewnetrzny"
                )
            ):
                suma += kwota

        return suma

    @loguj_awarie
    def wplac(self, numer, kwota):
        with self._lock:
            konto = self._pobierz_konto(numer)
            kwota = self._kwota_decimal(kwota)

            if kwota <= 0:
                raise ValueError(
                    "Kwota wplaty musi byc dodatnia"
                )

            konto.saldo += kwota
            konto.dodaj_wpis("wplata", kwota)

            return konto.saldo

    @loguj_awarie
    def wyplac(self, numer, kwota):
        with self._lock:
            konto = self._pobierz_konto(numer)

            if konto.status == "zablokowane":
                raise KontoZablokowane(
                    f"Konto {numer} jest zablokowane"
                )

            kwota = self._kwota_decimal(kwota)

            if kwota <= 0:
                raise ValueError(
                    "Kwota wyplaty musi byc dodatnia"
                )

            if kwota > konto.saldo:
                raise ValueError(
                    "Niewystarczajace srodki na koncie"
                )

            if (
                self._suma_dzisiejszych_wyplat(konto) + kwota
                > LIMIT_DZIENNY_WYPLAT
            ):
                raise ValueError(
                    "Przekroczono dzienny limit wyplat"
                )

            konto.saldo -= kwota
            konto.dodaj_wpis("wyplata", kwota)

            return konto.saldo

    @loguj_awarie
    def przelew(self, numer_z, numer_do, kwota):
        with self._lock:
            konto_z = self._pobierz_konto(numer_z)
            konto_do = self._pobierz_konto(numer_do)

            if konto_z.status == "zablokowane":
                raise KontoZablokowane(
                    f"Konto {numer_z} jest zablokowane"
                )

            kwota = self._kwota_decimal(kwota)

            if kwota <= 0:
                raise ValueError(
                    "Kwota przelewu musi byc dodatnia"
                )

            if kwota > LIMIT_PRZELEWU:
                raise ValueError(
                    "Przelew przekracza dzienny limit systemowy"
                )

            if kwota > konto_z.saldo:
                raise ValueError(
                    "Niewystarczajace srodki na koncie zrodlowym"
                )

            if konto_z is konto_do:
                raise ValueError(
                    "Przelew na to samo konto jest niedozwolony"
                )

            if (
                self._suma_dzisiejszych_wyplat(konto_z) + kwota
                > LIMIT_DZIENNY_WYPLAT
            ):
                raise ValueError(
                    "Przekroczono dzienny limit przelewow wychodzacych"
                )

            konto_z.saldo -= kwota
            konto_do.saldo += kwota

            konto_z.dodaj_wpis(
                "przelew_wychodzacy", kwota
            )
            konto_do.dodaj_wpis(
                "przelew_przychodzacy", kwota
            )

            return konto_z.saldo

    @loguj_awarie
    def nalicz_odsetki(
        self,
        numer,
        roczna_stopa_procentowa,
        miesiace
    ):
        with self._lock:
            konto = self._pobierz_konto(numer)
            stopa = self._kwota_decimal(
                roczna_stopa_procentowa
            )

            if stopa < 0:
                raise ValueError(
                    "Stopa procentowa nie moze byc ujemna"
                )

            if (
                isinstance(miesiace, bool)
                or not isinstance(miesiace, int)
                or not 0 <= miesiace <= 1200
            ):
                raise ValueError(
                    "Liczba miesiecy musi byc liczba calkowita z zakresu 0-1200"
                )

            stopa_miesieczna = (
                stopa / Decimal("12") / Decimal("100")
            )

            for _ in range(miesiace):
                odsetki = (
                    konto.saldo * stopa_miesieczna
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )

                konto.saldo += odsetki
                konto.dodaj_wpis("odsetki", odsetki)

            return konto.saldo

    @loguj_awarie
    def historia_w_okresie(self, numer, od, do):
        konto = self._pobierz_konto(numer)

        if not isinstance(od, date) or not isinstance(do, date):
            raise ValueError(
                "Daty musza byc obiektami datetime.date"
            )

        if od > do:
            raise ValueError(
                "Data poczatkowa nie moze byc pozniejsza od koncowej"
            )

        return [
            (data_operacji, typ, kwota)
            for data_operacji, typ, kwota in konto.historia
            if od <= data_operacji <= do
        ]

    @loguj_awarie
    def wyciag_miesieczny(self, numer, rok, miesiac):
        konto = self._pobierz_konto(numer)

        if (
            isinstance(rok, bool)
            or not isinstance(rok, int)
            or not 1 <= rok <= 9999
        ):
            raise ValueError("Nieprawidlowy rok")

        if (
            isinstance(miesiac, bool)
            or not isinstance(miesiac, int)
            or not 1 <= miesiac <= 12
        ):
            raise ValueError("Miesiac musi nalezec do zakresu 1-12")

        wplaty = Decimal("0")
        wyplaty = Decimal("0")

        for data_operacji, typ, kwota in konto.historia:
            if (
                data_operacji.year == rok
                and data_operacji.month == miesiac
            ):
                if typ in (
                    "wplata",
                    "przelew_przychodzacy",
                    "odsetki"
                ):
                    wplaty += kwota
                elif typ in (
                    "wyplata",
                    "przelew_wychodzacy",
                    "przelew_zewnetrzny"
                ):
                    wyplaty += kwota

        return {
            "wplaty": wplaty,
            "wyplaty": wyplaty,
            "saldo_koncowe": konto.saldo
        }

    @loguj_awarie
    def przelew_zewnetrzny(
        self,
        numer_z,
        numer_konta_docelowego_zewn,
        kwota,
        procesor=None
    ):
        with self._lock:
            konto_z = self._pobierz_konto(numer_z)

            if konto_z.status == "zablokowane":
                raise KontoZablokowane(
                    f"Konto {numer_z} jest zablokowane"
                )

            if (
                not isinstance(numer_konta_docelowego_zewn, str)
                or not numer_konta_docelowego_zewn.strip()
            ):
                raise ValueError(
                    "Numer konta docelowego nie moze byc pusty"
                )

            kwota = self._kwota_decimal(kwota)

            if kwota <= 0:
                raise ValueError(
                    "Kwota przelewu musi byc dodatnia"
                )

            if kwota > konto_z.saldo:
                raise ValueError(
                    "Niewystarczajace srodki na koncie zrodlowym"
                )

            if (
                self._suma_dzisiejszych_wyplat(konto_z) + kwota
                > LIMIT_DZIENNY_WYPLAT
            ):
                raise ValueError(
                    "Przekroczono dzienny limit wyplat"
                )

            proc = (
                procesor
                if procesor is not None
                else ZewnetrznyProcesor()
            )

            # Najpierw wysylamy przelew.
            # Jesli procesor zglosi blad, saldo nie zostanie zmienione.
            wynik = proc.wyslij_przelew(
                numer_konta_docelowego_zewn,
                kwota
            )

            konto_z.saldo -= kwota
            konto_z.dodaj_wpis(
                "przelew_zewnetrzny", kwota
            )

            return wynik


# ============================================================
# POMOCNICZE GENEROWANIE NUMEROW KONT TESTOWYCH
# ============================================================

def poprawny_numer(prefiks):
    """
    Generuje 10-cyfrowy numer z prawidlowa cyfra kontrolna.
    """
    cyfry = str(prefiks).zfill(9)

    if len(cyfry) != 9 or not cyfry.isascii() or not cyfry.isdigit():
        raise ValueError("Prefiks musi miec najwyzej 9 cyfr")

    kontrolna = sum(int(c) for c in cyfry) % 10
    return cyfry + str(kontrolna)


# ============================================================
# TESTY EKSPLORACYJNE
# ============================================================

def uruchom_testy_eksploracyjne():
    bank = BankSystem()

    nr1 = poprawny_numer("123456789")
    nr2 = poprawny_numer("000000001")
    nr3 = poprawny_numer("000000002")

    bank.utworz_konto(nr1, "Jan Kowalski", 10000)
    bank.utworz_konto(nr2, "Anna Nowak", 500)
    bank.utworz_konto(nr3, "Konto testowe", 0)

    # Deterministyczny procesor: zawsze dziala.
    procesor_ok = ZewnetrznyProcesor(
        prawdopodobienstwo_awarii=0,
        generator=random.Random(1)
    )

    # Deterministyczny procesor: zawsze zawodzi.
    procesor_awaria = ZewnetrznyProcesor(
        prawdopodobienstwo_awarii=1,
        generator=random.Random(1)
    )

    operacje = [
        (
            "Poprawna wplata",
            lambda: bank.wplac(nr1, 500),
            True
        ),
        (
            "Wplata zerowa",
            lambda: bank.wplac(nr1, 0),
            False
        ),
        (
            "Wplata ujemna",
            lambda: bank.wplac(nr1, -100),
            False
        ),
        (
            "Wplata z tekstowa kwota",
            lambda: bank.wplac(nr1, "abc"),
            False
        ),
        (
            "Wplata z bardzo duza kwota",
            lambda: bank.wplac(nr1, 10**100),
            True
        ),
        (
            "Operacja na nieistniejacym koncie",
            lambda: bank.wplac("9999999999", 100),
            False
        ),
        (
            "Poprawna wyplata",
            lambda: bank.wyplac(nr1, 200),
            True
        ),
        (
            "Wyplata powyzej salda",
            lambda: bank.wyplac(nr3, 20000),
            False
        ),
        (
            "Wyplata ujemna",
            lambda: bank.wyplac(nr1, -50),
            False
        ),
        (
            "Wyplata ponad limit dzienny",
            lambda: bank.wyplac(nr1, 6000),
            False
        ),
        (
            "Poprawny przelew",
            lambda: bank.przelew(nr1, nr2, 100),
            True
        ),
        (
            "Przelew na wlasne konto",
            lambda: bank.przelew(nr1, nr1, 100),
            False
        ),
        (
            "Przelew kwoty ujemnej",
            lambda: bank.przelew(nr1, nr2, -50),
            False
        ),
        (
            "Przelew ponad limit systemowy",
            lambda: bank.przelew(nr1, nr2, 2000000),
            False
        ),
        (
            "Przelew na nieistniejace konto",
            lambda: bank.przelew(nr1, "9999999999", 100),
            False
        ),
        (
            "Zablokowanie konta",
            lambda: bank.zablokuj_konto(nr2),
            True
        ),
        (
            "Wyplata z zablokowanego konta",
            lambda: bank.wyplac(nr2, 100),
            False
        ),
        (
            "Odblokowanie konta",
            lambda: bank.odblokuj_konto(nr2),
            True
        ),
        (
            "Naliczanie odsetek",
            lambda: bank.nalicz_odsetki(nr1, 5, 1),
            True
        ),
        (
            "Ujemna stopa procentowa",
            lambda: bank.nalicz_odsetki(nr1, -5, 1),
            False
        ),
        (
            "Ujemna liczba miesiecy",
            lambda: bank.nalicz_odsetki(nr1, 5, -1),
            False
        ),
        (
            "Historia z poprawnym zakresem",
            lambda: bank.historia_w_okresie(
                nr1, date.today(), date.today()
            ),
            True
        ),
        (
            "Historia z odwroconym zakresem",
            lambda: bank.historia_w_okresie(
                nr1, date.today(), date(2020, 1, 1)
            ),
            False
        ),
        (
            "Wyciag miesieczny",
            lambda: bank.wyciag_miesieczny(
                nr1, date.today().year, date.today().month
            ),
            True
        ),
        (
            "Wyciag z nieprawidlowym miesiacem",
            lambda: bank.wyciag_miesieczny(nr1, 2026, 13),
            False
        ),
        (
            "Przelew zewnetrzny poprawny",
            lambda: bank.przelew_zewnetrzny(
                nr1, "PL12345678901234567890123456",
                50, procesor_ok
            ),
            True
        ),
        (
            "Przelew zewnetrzny - awaria procesora",
            lambda: bank.przelew_zewnetrzny(
                nr1, "PL12345678901234567890123456",
                50, procesor_awaria
            ),
            False
        ),
        (
            "Przelew zewnetrzny - kwota ujemna",
            lambda: bank.przelew_zewnetrzny(
                nr1, "PL12345678901234567890123456",
                -10, procesor_ok
            ),
            False
        ),
    ]

    wyniki = []
    czas_start = time.perf_counter()

    print("\n=== START TESTOW EKSPLORACYJNYCH ===\n")

    for idx, (opis, operacja, oczekiwany_sukces) in enumerate(
        operacje, 1
    ):
        start_testu = time.perf_counter()

        try:
            rezultat = operacja()
            czas = time.perf_counter() - start_testu

            if oczekiwany_sukces:
                status = "PASS"
                szczegoly = f"Wynik: {rezultat}"
            else:
                status = "FAIL"
                szczegoly = (
                    "Operacja powinna zakonczyc sie wyjatkiem, "
                    "ale zostala wykonana"
                )

        except Exception as exc:
            czas = time.perf_counter() - start_testu

            if oczekiwany_sukces:
                status = "FAIL"
                szczegoly = (
                    f"Nieoczekiwany wyjatek: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                status = "PASS"
                szczegoly = (
                    f"Oczekiwany wyjatek: "
                    f"{type(exc).__name__}: {exc}"
                )

        wyniki.append({
            "nr": idx,
            "opis": opis,
            "status": status,
            "szczegoly": szczegoly,
            "czas_s": czas
        })

    czas_testow = time.perf_counter() - czas_start

    print(
        f"{'NR':<4} | {'OPIS TESTU':<43} | "
        f"{'STATUS':<6} | SZCZEGOLY"
    )
    print("-" * 120)

    for wynik in wyniki:
        print(
            f"{wynik['nr']:<4} | "
            f"{wynik['opis']:<43} | "
            f"{wynik['status']:<6} | "
            f"{wynik['szczegoly']}"
        )

    liczba_testow = len(wyniki)
    zaliczone = sum(
        1 for wynik in wyniki
        if wynik["status"] == "PASS"
    )
    niezaliczone = liczba_testow - zaliczone

    print("-" * 120)
    print(
        f"Testy: {liczba_testow} | "
        f"PASS: {zaliczone} | FAIL: {niezaliczone}"
    )

    policz_wskazniki(
        wyniki,
        czas_testow,
        BankSystem
    )

    return wyniki


# ============================================================
# WSKAZNIKI JAKOSCIOWE
# ============================================================

def policz_wskazniki(wyniki, czas_testow, klasa_sut):
    """
    Raportuje:
    - liczbe wywolan,
    - liczbe niezaliczonych testow,
    - liczbe wyjatkow,
    - czas testow,
    - przyblizony czas na niezaliczony test,
    - szacunkowa gestosc niezaliczonych testow na 100 LOC.

    Nie utozsamia liczby niezaliczonych testow z liczba
    unikalnych defektow.
    """

    liczba_wywolan = len(wyniki)
    liczba_fail = sum(
        1 for wynik in wyniki
        if wynik["status"] == "FAIL"
    )

    # Liczba linii kodu klasy liczona z jej zrodla.
    try:
        zrodlo = inspect.getsource(klasa_sut)
        liczba_loc = sum(
            1
            for token in tokenize.generate_tokens(
                io.StringIO(zrodlo).readline
            )
            if token.type == tokenize.NEWLINE
            and token.line.strip()
        )
    except (OSError, TypeError, tokenize.TokenError):
        liczba_loc = 0

    # Jest to wskaznik orientacyjny, a nie klasyczne MTTF.
    czas_na_fail = (
        czas_testow / liczba_fail
        if liczba_fail > 0
        else math.inf
    )

    gestosc = (
        liczba_fail / liczba_loc * 100
        if liczba_loc > 0
        else math.nan
    )

    print("\n" + "=" * 25 + " RAPORT JAKOSCI " + "=" * 25)
    print(f"Liczba wywolan testowych: {liczba_wywolan}")
    print(f"Testy niezaliczone: {liczba_fail}")
    print(f"Czas wykonania testow: {czas_testow:.6f} s")

    if math.isfinite(czas_na_fail):
        print(
            "Orientacyjny czas testow na jeden FAIL: "
            f"{czas_na_fail:.6f} s"
        )
    else:
        print(
            "Orientacyjny czas testow na jeden FAIL: "
            "brak FAIL w serii"
        )

    if liczba_loc > 0:
        print(f"Linie kodu klasy BankSystem: {liczba_loc}")
        print(
            "Gestosc niezaliczonych testow na 100 LOC: "
            f"{gestosc:.2f}"
        )
    else:
        print(
            "Nie udalo sie automatycznie policzyc LOC. "
            "Uruchom kod z pliku .py."
        )

    print(
        "Uwaga: FAIL oznacza niezgodnosc wyniku z oczekiwaniem "
        "testu, a nie automatycznie nowy defekt."
    )
    print("=" * 70)


# ============================================================
# URUCHOMIENIE
# ============================================================

if __name__ == "__main__":
    uruchom_testy_eksploracyjne()