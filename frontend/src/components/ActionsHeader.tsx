import { Table, VisuallyHidden } from "@mantine/core";

/** Column header for a table's trailing row-action buttons, so the column has a name for screen readers. */
export function ActionsHeader() {
  return (
    <Table.Th>
      <VisuallyHidden>Actions</VisuallyHidden>
    </Table.Th>
  );
}
