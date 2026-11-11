import { Alert, Button, Card, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useState } from "react";
import { ApiError, api } from "../api";

export function Login() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useForm({
    initialValues: { username: "", password: "" },
  });

  return (
    <Stack mih="100dvh" align="center" justify="center" p="md" className="mor-login">
      <Card withBorder shadow="lg" padding="xl" radius="lg" w="100%" maw={400}>
        <Stack gap="md">
          <img
            src="/brand/primary_stacked.png"
            alt="MOR — MoreOpenRepeater"
            className="mor-login-lockup"
            width={274}
            height={156}
          />
          <Text c="dimmed">Sign in to manage your repeater.</Text>
          <form
            onSubmit={form.onSubmit(async (values) => {
              setBusy(true);
              setError(null);
              try {
                await api("/api/login", { method: "POST", json: values });
                location.replace("/");
              } catch (err) {
                setError(err instanceof ApiError ? err.message : String(err));
              } finally {
                setBusy(false);
              }
            })}
          >
            <Stack gap="sm">
              <TextInput label="Username" autoComplete="username" required {...form.getInputProps("username")} />
              <PasswordInput label="Password" autoComplete="current-password" required {...form.getInputProps("password")} />
              {error ? (
                <Alert color="red" role="alert">
                  {error}
                </Alert>
              ) : null}
              <Button type="submit" loading={busy} fullWidth>
                Sign in
              </Button>
            </Stack>
          </form>
        </Stack>
      </Card>
    </Stack>
  );
}
