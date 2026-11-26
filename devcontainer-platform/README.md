# Dev Container Platform

A interface usa HTTPS em `devcontainer-platform.lab.wilgnne.dev`. O gateway dos workspaces usa SSH na porta TCP `2223` do mesmo host. O serviço usa as imagens do GHCR para Linux amd64 e requer Docker Engine com API 1.45 ou posterior. [Guia de instalação upstream](https://github.com/wilgnne/devcontainer-platform/blob/v0.1.0/docs/INSTALL.md)

## DNS e Traefik

Aponte o domínio da interface para o host do Traefik. Se definir outro domínio em `PLATFORM_SSH_HOST`, aponte-o também para o Traefik. O roteador web usa o entrypoint `websecure` com TLS e encaminha para a porta interna `8080` pela rede Docker externa `proxy`. O Traefik precisa estar nessa rede, com o provedor Docker habilitado e um certificado válido para o domínio.

Adicione um entrypoint TCP dedicado à configuração estática do Traefik existente:

```yaml
entryPoints:
  dcpssh:
    address: ":2223/tcp"
```

Se o Traefik rodar em Docker, publique `2223:2223/tcp` no serviço do Traefik e permita essa porta no firewall para os clientes. Aplique a configuração no proxy. O roteador TCP encaminha SSH para a porta interna `2223` da plataforma. Reserve `dcpssh` para esse gateway, pois a regra ``HostSNI(`*`)`` aceita todas as conexões do entrypoint. Se mudar o nome do entrypoint ou a porta externa, ajuste `TRAEFIK_SSH_ENTRYPOINT` ou `PLATFORM_SSH_PORT` no `.env`.

## Implantação

Execute no host Docker a partir da raiz do homeprod:

```sh
cd devcontainer-platform
cp .env.example .env
stat -Lc %g /var/run/docker.sock
```

Preencha `DOCKER_SOCKET_GID` no `.env` com o valor retornado. Se o socket estiver em outro caminho, defina `DOCKER_SOCKET_SOURCE` e consulte o GID desse caminho. Use origens confiáveis para os workspaces, pois a plataforma usa o socket Docker e executa comandos dos projetos durante a preparação.

Entre no GHCR com uma conta que tenha acesso aos dois pacotes privados. Use um token clássico do GitHub com escopo `read:packages` quando o Docker pedir a senha.

```sh
docker login ghcr.io --username SEU_USUARIO_GITHUB
docker compose config -q
docker compose pull checkout-helper platform
docker compose up -d
docker compose ps
docker compose exec platform /platform setup-code -data-dir /data
```

Abra `https://devcontainer-platform.lab.wilgnne.dev/` e use o código para criar a conta administrativa. Depois, cadastre sua chave pública em **Configurações** e use o comando SSH exibido no detalhe de um workspace preparado. O auxiliar `checkout-helper` fica com escala zero e é iniciado pela plataforma sob demanda.

Preserve o nome do projeto Compose e os volumes ao atualizar o serviço. `platform-data` guarda o banco e a identidade SSH do gateway. `github-auth` e `codex-auth` guardam credenciais.
