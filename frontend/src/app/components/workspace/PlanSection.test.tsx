import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlanSection } from "@/app/components/workspace/PlanSection";
import type { TodoItem } from "@/app/types/types";

function todo(id: string, status: TodoItem["status"], content = `Task ${id}`): TodoItem {
  return { id, content, status };
}

function renderPlan(todos: TodoItem[], props: { open?: boolean; onToggle?: () => void } = {}) {
  return render(
    <PlanSection todos={todos} open={props.open ?? true} onToggle={props.onToggle ?? vi.fn()} />
  );
}

describe("PlanSection", () => {
  it("renders the empty state when there are no todos", () => {
    renderPlan([]);

    expect(screen.getByText("Plan")).toBeInTheDocument();
    expect(screen.getByText("No tasks yet")).toBeInTheDocument();
    expect(
      screen.getByText("The agent's plan will appear here as soon as work starts.")
    ).toBeInTheDocument();
    // No progress block and no count badge for an empty plan.
    expect(screen.queryByText("Progress")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows the todo count badge in the header", () => {
    renderPlan([
      todo("1", "pending"),
      todo("2", "in_progress"),
      todo("3", "completed"),
      todo("4", "completed"),
    ]);

    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("groups todos by status in the order in_progress, pending, completed", () => {
    renderPlan([
      todo("c1", "completed", "Ship the report"),
      todo("p1", "pending", "Draft the summary"),
      todo("a1", "in_progress", "Research the company"),
    ]);

    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings.map((h) => h.textContent)).toEqual(["In Progress", "Pending", "Completed"]);

    // Each todo renders inside its own status group.
    const groupFor = (label: string) =>
      screen.getByRole("heading", { level: 3, name: label }).parentElement as HTMLElement;
    expect(within(groupFor("In Progress")).getByText("Research the company")).toBeInTheDocument();
    expect(within(groupFor("Pending")).getByText("Draft the summary")).toBeInTheDocument();
    expect(within(groupFor("Completed")).getByText("Ship the report")).toBeInTheDocument();
  });

  it("omits status groups that have no todos", () => {
    renderPlan([todo("1", "completed")]);

    expect(
      screen.queryByRole("heading", { level: 3, name: "In Progress" })
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "Pending" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Completed" })).toBeInTheDocument();
  });

  it("renders the completion percentage (2 of 4 completed renders as 50%)", () => {
    renderPlan([
      todo("1", "completed"),
      todo("2", "completed"),
      todo("3", "in_progress"),
      todo("4", "pending"),
    ]);

    expect(screen.getByText("Progress")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("shows the Active pulse badge only while a task is in progress", () => {
    const { rerender } = renderPlan([todo("1", "in_progress"), todo("2", "completed")]);
    expect(screen.getByText("Active")).toBeInTheDocument();

    rerender(
      <PlanSection
        todos={[todo("1", "completed"), todo("2", "completed")]}
        open
        onToggle={vi.fn()}
      />
    );
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("renders a status icon for every todo row", () => {
    renderPlan([
      todo("a", "in_progress"),
      todo("b", "pending"),
      todo("c", "completed"),
      todo("d", "completed"),
    ]);

    for (const label of ["In Progress", "Pending", "Completed"]) {
      const group = screen.getByRole("heading", { level: 3, name: label })
        .parentElement as HTMLElement;
      const rows = group.querySelectorAll(":scope > div > div");
      const icons = group.querySelectorAll("svg");
      expect(rows.length).toBeGreaterThan(0);
      expect(icons).toHaveLength(rows.length);
    }
  });

  it("hides the body when the card is collapsed", () => {
    renderPlan([todo("1", "pending")], { open: false });

    expect(screen.getByText("Plan")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Plan/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Progress")).not.toBeInTheDocument();
    expect(screen.queryByText("Task 1")).not.toBeInTheDocument();
  });

  it("invokes onToggle when the header is clicked", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    renderPlan([todo("1", "pending")], { onToggle });

    await user.click(screen.getByRole("button", { name: /Plan/ }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
