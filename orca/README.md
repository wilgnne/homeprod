# Orca com Coder CLI

A imagem instala o Coder CLI pelo instalador oficial, em modo `standalone`
(`/usr/local/bin/coder`). Os ambientes de desenvolvimento ficam no servidor
Coder; o container do Orca atua como cliente.

Para construir e iniciar, configure `.env` a partir de `.env.example` e execute
neste diretório:

```bash
docker compose up -d --build
```

## Runtime e APIs SSH

O Compose fixa o Orca em `v1.4.253`. As APIs `ssh.listEditableTargets`,
`ssh.addTarget`, `ssh.updateTarget`, `ssh.removeTarget` e `repo.addRemote`
já existem na tag `v1.4.228` e são mencionadas nas notas de `v1.4.229`.
A versão foi atualizada para a release mais recente publicada em 7 de novembro de 2026.

O Compose define `ORCA_SERVE_RUNTIME=electron`: em `v1.4.253`, o runtime
`orcad`, usado por padrão em `serve`, ainda não oferece suporte aos hosts SSH.
O runtime Electron é necessário para essa integração, mesmo sem janela gráfica.

Settings → SSH usa o cadastro local, bloqueado no cliente web. Para cadastrar
um host pertencente ao servidor pareado, use o fluxo Add project → SSH host,
conforme descrito abaixo. Ele chama as APIs do servidor. As APIs de administração
não são permitidas ao cliente mobile; faça esse preparo em um cliente com acesso
workspace (por exemplo, Orca Desktop pareado). O fluxo completo com Coder ainda
precisa ser validado em execução.

Para aplicar a configuração a uma instalação existente, neste diretório:

```bash
docker compose up -d --build orca-server
```

Fontes: [APIs na tag v1.4.228](https://github.com/stablyai/orca/blob/v1.4.228/src/main/runtime/rpc/methods/ssh.ts),
[release v1.4.229](https://github.com/stablyai/orca/releases/tag/v1.4.229),
[seleção do runtime em v1.4.253](https://github.com/stablyai/orca/blob/v1.4.253/src/main/orcad/orcad-local-serve-selection.ts) e
[limitações do cliente web em v1.4.253](https://github.com/stablyai/orca/blob/v1.4.253/src/renderer/src/web/preload-api/web-terminal-api.ts).

## Login manual e SSH

Abra um bash como o usuário da aplicação para gravar a sessão no local correto:

```bash
docker compose exec --user orca orca-server bash
coder login https://coder.lab.wilgnne.dev
coder config-ssh --yes
```

Siga as instruções interativas do login. Para ferramentas que precisam descobrir
os workspaces lendo entradas individuais em `~/.ssh/config`, use
`coder config-ssh --yes --no-wildcard` após o login e repita quando os workspaces
mudarem.

`config-ssh` precisa do login: no CLI v2.39.0, a execução com um diretório de
configuração vazio retorna `You are not logged in`. A inicialização do container
não tenta autenticar nem configurar SSH automaticamente.

## Registrar workspaces do Coder no Orca Server

1. Aplique a imagem e a configuração atualizadas com `docker compose up -d --build orca-server`.
2. No bash do container, como `orca`, prepare e teste o acesso:

   ```bash
   docker compose exec --user orca orca-server bash
   coder login https://coder.lab.wilgnne.dev
   coder config-ssh --yes --no-wildcard
   coder list
   awk '/^Host / {print}' ~/.ssh/config
   ```

   Escolha o alias exato gerado para o workspace e teste, substituindo `ALIAS`:

   ```bash
   ssh -G ALIAS
   ssh ALIAS 'id; pwd'
   ```

   O teste deve funcionar a partir do container, usando a sessão persistida.
   O Coder configura o ProxyCommand; não use o domínio do painel Coder como
   endereço SSH do workspace. Ao alterar os workspaces, gere o config novamente.

3. Em um Orca Desktop atualizado, pareie com o Orca Server do container.
   No fluxo **Add project**, selecione esse servidor como host proprietário.
   Abra a opção de projeto em **SSH host** e clique **Add SSH host**.
   O título deve ser **Add SSH host to <nome do servidor>**: esse formulário salva
   o alvo no container, não no computador do cliente. Evite Settings → SSH local.
4. Informe um rótulo e, em **Host or alias**, o alias exato validado no container.
   Use o usuário e a porta resolvidos por `ssh -G ALIAS`; deixe o campo de chave
   vazio para aproveitar as identidades do SSH config. Caso seja necessário
   informar ProxyCommand em Advanced, copie o valor resolvido no container,
   cujos caminhos devem ser válidos dentro dele.
5. Salve, conecte o alvo, selecione o caminho do repositório dentro do workspace
   do Coder e finalize em **Add project on SSH host**.
6. Pareie o aplicativo mobile com o mesmo Orca Server e valide a retomada do
   projeto e da sessão. Depois teste a reconexão com o workspace do Coder parado:
   a ativação sob demanda depende da conexão Coder e das políticas desse workspace.

O cadastro via Settings → SSH do cliente web continua bloqueado; o fluxo de
projeto usa uma API diferente. Se o formulário de SSH do servidor não aparecer
no web, use o Desktop para esse cadastro inicial. Não há comando `orca host add`
na CLI que foi consultada. A existência dessas telas/APIs foi verificada no
código da release; cadastro, conexão e retomada mobile não foram testados no seu
servidor em execução.

Fontes: [cadastro SSH pertencente ao servidor](https://github.com/stablyai/orca/blob/v1.4.253/src/renderer/src/components/sidebar/host-admission/ssh/AddServerSshHostDialog.tsx),
[projeto em host SSH](https://github.com/stablyai/orca/blob/v1.4.253/src/renderer/src/components/sidebar/project-admission/dialog/AddRepoRemoteStep.tsx) e
[configuração SSH do Coder](https://coder.com/docs/reference/cli/config-ssh).

## Persistência

O Compose mantém os seguintes volumes nomeados:

| Volume | Montagem | Conteúdo |
| --- | --- | --- |
| `orca-home` | `/home/orca` | Dados e configurações do Orca |
| `coder-config` | `/home/orca/.config/coderv2` | Configuração e sessão do Coder (`CODER_CONFIG_DIR`) |
| `coder-ssh` | `/home/orca/.ssh` | Configuração SSH, chaves e hosts conhecidos |

O entrypoint prepara os diretórios do Coder e SSH com proprietário `orca` e modo
`700`. As montagens preservam o login e a configuração SSH ao recriar o container.
`docker compose down -v` remove os volumes, incluindo a sessão salva.

Referências: [instalador oficial](https://coder.com/install.sh),
[acesso aos workspaces](https://coder.com/docs/user-guides/workspace-access) e
[config-ssh](https://coder.com/docs/reference/cli/config-ssh).
