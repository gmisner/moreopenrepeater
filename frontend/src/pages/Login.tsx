import { Alert, Button, Card, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useState } from "react";
import { ApiError, api } from "../api";
import { BrandMark } from "../layout/BrandMark";

export function Login() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useForm({
    initialValues: { username: "", password: "" },
  });

  return (
    <Stack mih="100dvh" align="center" justify="center" p="md">
      <Card withBorder shadow="lg" padding="xl" radius="lg" w="100%" maw={380}>
        <Stack gap="md">
          <BrandMark size={32} />
          <div>
            <Title order={1}>moreopenrepeater</Title>
            <Text c="dimmed">Sign in to manage the repeater.</Text>
          </div>
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
