# Raven Cortex — stav 2026-09-05

> Historický záznam. Nejde o stav aktuální pracovní kopie ani o potvrzení kompletně hotového Cortexu. Aktuální opravy a otevřené body jsou v WORK-STATUS-2026-09-06.md. Instalační distribuce uvedená níže je překonaná: nyní pouze portable bez nového výslovného požadavku na instalátor. Aktuální flash identifikuj markerem a sériovým číslem, nikoli písmeny v tomto starém textu.

Verze projektu zůstává 1.2. Tento soubor popisuje skutečně ověřený lokální stav; nejde o potvrzení nahrání na GitHub.

## Dokončeno a ověřeno

- Hlavní vývojová kopie je `C:\projektjarvis`, přenosná kopie je `F:\Raven-1.2`.
- Nový Cortex, paměťové učení, evaluace a navazující agentní moduly jsou součástí zdrojů i instalačního balíku.
- Lokální model používá přímé Ollama chat API s omezeným kontextem, vypnutým skrytým přemýšlením a dostatečným časem na první načtení.
- Skutečný lokální model na PC i flashdisku odpověděl `42`; kontrolor odpověď přijal.
- Finální automatická sada: 137 testů prošlo, 1 volitelný test byl přeskočen.
- JavaScript a PowerShell mají platnou syntaxi, kontrola změn prošla a produkční npm audit hlásí 0 známých zranitelností.
- Čistá instalace finálního `Raven-1.2-Setup.exe` do prázdné složky dokončila závislosti a vytvořila ověřovací záznam se stavem `passed`.
- Skutečný nainstalovaný Electron HUD, terminál a souborový agent prošly testem vytvoření, přečtení a odstranění souboru.
- Aktualizace finálním EXE zachovala data v `runtime` i uživatelský soubor v kořeni. Odinstalace odstranila programové soubory, ale obě uživatelská data zachovala.
- Přenosná kopie prošla na `C:` i přímo z `F:`. Test přes alternativní písmeno `R:` také prošel; launcher automaticky opravuje cesty přenosného Pythonu podle aktuálního umístění.
- Po každém provozním testu zůstalo 0 vlastních procesů a 0 portů Ravenu.
- Vlastní Ollama na PC a flashdisku byla porovnána v 66 souborech bez jediného rozdílu. Modely a runtime se při poslední synchronizaci zbytečně nekopírovaly znovu.
- Zdrojová část PC a flashdisku je shodná v 2 277 kontrolovaných souborech.

## Záměrně odloženo

- Trénování vah LoRA/QLoRA nebylo spuštěno. Uživatel požaduje nový samostatný příkaz `start` až po dokončení všeho ostatního.
- Čistá instalace byla ověřena v izolované složce současného Windows počítače, nikoli v samostatném virtuálním počítači.
- Další rozšiřování plně autonomního rollbacku, specializovaných agentů a experimentálních integrací zůstává budoucí práce, nikoli skrytá součást tohoto ověření.

## Distribuce

- GitHub nebyl v tomto pracovním kroku aktualizován. Nahrání vyžaduje samostatný výslovný příkaz uživatele `nahraj na github`.
- Finální distribuce má obsahovat instalační EXE, blockmapu, `latest.yml`, README, release notes a kontrolní součty SHA-256.
