import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CandidateModeSwitch } from "./CandidatesPage";
import { CandidateSelectionCheckbox } from "./CandidateBulkActions";

describe("candidate accessibility controls", () => {
  it("uses native pressed buttons for the candidate source mode", async () => {
    const user = userEvent.setup();
    const chooseMode = vi.fn();
    render(<CandidateModeSwitch mode="companies" chooseMode={chooseMode} />);

    const group = screen.getByRole("group", { name: "Candidate source mode" });
    const tracked = screen.getByRole("button", { name: "Tracked Companies" });
    const discovery = screen.getByRole("button", { name: "Discovery" });

    expect(group).toBeInTheDocument();
    expect(tracked).toHaveAttribute("aria-pressed", "true");
    expect(discovery).toHaveAttribute("aria-pressed", "false");

    await user.click(discovery);
    expect(chooseMode).toHaveBeenCalledWith("discovery");
  });

  it("exposes a named, keyboard-usable candidate selection checkbox", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <CandidateSelectionCheckbox
        checked={false}
        label="Select synthetic candidate"
        onChange={onChange}
      />
    );

    const checkbox = screen.getByRole("checkbox", { name: "Select synthetic candidate" });
    await user.tab();
    expect(checkbox).toHaveFocus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
