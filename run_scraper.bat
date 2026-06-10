@echo off
:: Run the AustralianTeachers data collector using the shared venv
:: Leave this window open so you can watch progress

SET VENV=G:\My Drive\All\Work\Academia\Articles\Articles written\Manfluencers-Gaming-Reddit\venv\Scripts\python.exe
SET SCRIPT=G:\My Drive\All\Work\Academia\Articles\Articles written\Far Right School Settings\collect_data.py

echo Starting scraper...
"%VENV%" "%SCRIPT%"
echo.
echo Done. Press any key to close.
pause
