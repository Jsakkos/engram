import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AmendTitleModal } from "./AmendTitleModal";

describe("AmendTitleModal", () => {
  it("submits an extra amendment", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AmendTitleModal
        title={{ id: 2265, matchedEpisode: "S03E10", titleIndex: 24 }}
        seasonEpisodes={[10, 11, 12, 13]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /mark as extra/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onSubmit).toHaveBeenCalledWith({ kind: "extra" });
  });
});
