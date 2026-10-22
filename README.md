# Complementos QGIS da Solo

Repositório de complementos do QGIS da Solo Topografia e Georreferenciamento.
O QGIS lê a lista (`plugins.xml`) e os zips direto daqui. Funciona de qualquer lugar com internet, inclusive fora da rede da Solo.

| Complemento | O que tem |
|---|---|
| **Ferramentas Solo** | Barra com Desconto de Beiral, Remover Filetes, Guias CAD com medidas ao vivo, Comprimento e Área, Preencher Campo, Seleção por Sobreposição, Importar/Exportar DXF e Relatório de Produtividade, mais os scripts da Solo em Processamento > Ferramentas Solo |

Autor: Edu Gabriel Castilho Sembalista.
Menção honrosa: Joel Victor Feitosa da Silva, autor do complemento original "Comprimento e Área".

## Instalar no QGIS (uma vez por computador)

1. **Complementos > Gerenciar e Instalar Complementos > Configurações**
2. Marque **Verificar atualizações ao iniciar**.
3. Em **Repositórios de complementos**, clique em **Adicionar...** e preencha:
   - Nome: `Solo`
   - URL: `https://raw.githubusercontent.com/edugcs-gif/qgis-solo/main/plugins.xml`

   Se já existir o repositório "Solo" da pasta de rede, selecione-o, clique em **Editar...** e troque só a URL.
4. Clique em **OK** e depois em **Recarregar todos os repositórios**.
5. Na aba **Todos**, procure **Ferramentas Solo** e clique em **Instalar**.
   Quem já instalou pelo ZIP ou pela pasta de rede não precisa reinstalar, porque o QGIS reconhece.

Daí em diante, cada versão nova aparece em **Atualizável** e o QGIS avisa ao abrir.

## Publicar uma versão nova

O código-fonte fica em `codigo/<complemento>/`.

1. Faça a alteração no código.
2. Aumente o `version=` no `codigo/<complemento>/metadata.txt` e acrescente uma linha no `changelog=`.
3. Rode o script, que gera o zip, apaga o zip da versão anterior e atualiza o `plugins.xml`:
   ```
   python gerar_plugins_xml.py
   ```
4. Envie para o GitHub:
   ```
   git add -A
   git commit -m "Ferramentas Solo 3.3 - o que mudou"
   git push
   ```

O GitHub pode levar alguns minutos para mostrar a versão nova.

Para colocar outro complemento no repositório, crie a pasta dele em `codigo/` (com o `metadata.txt`) e rode o script. Ele entra na mesma lista.

## Observações

- O QGIS identifica o complemento pelo nome da pasta de dentro do zip (`ferramentas_solo`). O script já cuida disso, então o zip pode ter a versão no nome.
- O script também funciona numa pasta de rede: fora de um clone do git, ele usa o endereço `file://` da própria pasta.
- Este repositório é **público**: qualquer pessoa consegue ver e baixar o código.
