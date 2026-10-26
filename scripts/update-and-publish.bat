@echo off
setlocal

pushd "%~dp0.." || exit /b 1

call npm run data:update
if errorlevel 1 goto :failed

git add public/data/summary.json public/data/states
git diff --cached --quiet
if not errorlevel 1 (
  echo Nenhum dado novo para publicar.
  popd
  exit /b 0
)

for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "$ufs = git diff --cached --name-only -- public/data/states ^| ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_).ToUpperInvariant() } ^| Sort-Object -Unique; $ufs -join ', '"`) do set "UFS=%%U"
if not defined UFS set "UFS=resumo"

git commit -m "data: update %UFS%"
if errorlevel 1 goto :failed

git push origin main
if errorlevel 1 goto :failed

call npm run build
if errorlevel 1 goto :failed

npx --yes wrangler@latest pages deploy dist --project-name urnas-pos-17h --branch main
if errorlevel 1 goto :failed

echo Publicado: %UFS%
popd
exit /b 0

:failed
echo Falhou. Nenhum push foi feito depois do erro.
popd
exit /b 1
