# Barbearia da Vinte

Site com agendamento online, area admin e persistencia em SQLite.

## Como rodar

```powershell
python app.py
```

Depois abra:

```text
http://127.0.0.1:8000
```

Painel administrativo:

```text
http://127.0.0.1:8000/admin
```

Credenciais iniciais:

- Usuario: `admin`
- Senha: `1234`

Voce pode trocar em hospedagem usando variaveis de ambiente:

- `ADMIN_USER`
- `ADMIN_PASS`

## O que faz

- Cliente escolhe servico, barbeiro, data e horario.
- Horario confirmado fica bloqueado para outros clientes.
- Admin cadastra barbeiros, e-mail dos barbeiros, servicos, valores e horarios.
- Admin visualiza e cancela/conclui agendamentos.
- O sistema tenta enviar e-mail para o barbeiro quando um horario e marcado.

## E-mail

Para envio real de e-mail, configure:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASS`
- `SMTP_FROM`

Se o SMTP nao estiver configurado, o agendamento ainda e salvo e o aviso fica registrado na tabela `email_outbox`.

## Publicar no Render

Configure:

- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py`
- Environment Variable: `HOST=0.0.0.0`

O Render define `PORT` automaticamente.
