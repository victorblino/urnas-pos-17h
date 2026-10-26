# Urnas pós 17h

Painel independente que audita, a partir dos logs oficiais das urnas do TSE, os votos registrados antes e depois das 17h03 de 25 de outubro de 2026, no segundo turno.

O projeto surgiu diante das dúvidas e questionamentos sobre quanto a prorrogação da votação pode ter interferido no resultado das eleições de 2026. A proposta é tornar esses registros verificáveis, seção por seção, exclusivamente com dados oficiais.

## Site

[eleicoes.vicli.dev](https://eleicoes.vicli.dev)

## Fonte

- [Dados de urna do TSE — segundo turno de 2026](https://resultados.tse.jus.br/oficial/app/index.html#/eleicao/6258/uf/br/cargo/1/vis/nominal/dados-de-urna/boletim-de-urna)
- Logs de eventos `VOTA`, incluindo o registro de votos computados e o início do encerramento da urna.

## Comandos

```powershell
# Instalar dependências
npm install

# Rodar o site localmente
npm run dev

# Baixar logs de uma UF
npm run data:download -- --uf sp --workers 8

# Baixar logs de todas as UFs
npm run data:download -- --uf all --workers 8

# Processar logs já baixados
npm run data:update

# Reprocessar mesmo sem arquivos novos
npm run data:update -- --force

# Processar, criar commit com as UFs atualizadas e enviar ao GitHub
& ".\scripts\update-and-publish.bat"
```

## Metodologia

O corte é 17h03, horário de Brasília: momento em que foi divulgada a decisão de prorrogar a votação. O script considera exclusivamente arquivos do segundo turno (`o03221`) e converte os horários locais dos logs para Brasília antes da comparação.

Para não reprocessar dados sem necessidade, o script cria uma impressão digital dos arquivos `.jez` locais. Se nenhum arquivo novo ou alterado for encontrado, a atualização é ignorada.
