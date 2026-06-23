import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AmendTitleModal } from "./AmendTitleModal";

describe("AmendTitleModal", () => {
  it("submits an extra amendment", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AmendTitleModal
        title={{ id: 2265, matchedEpisode: "S03E10", titleIndex: 24 }}
        season={3}
        seasonEpisodes={[10, 11, 12, 13]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /mark as extra/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onSubmit).toHaveBeenCalledWith({ kind: "extra" });
  });

  it("labels options and submits episode codes using the season prop, not the matched episode", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AmendTitleModal
        // Track is (wrongly) matched to S01 — the modal must still offer S02.
        title={{ id: 1, matchedEpisode: "S01E01", titleIndex: 6 }}
        season={2}
        seasonEpisodes={[1, 2, 3]}
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    // Dropdown options reflect the disc's season, not the matched episode.
    expect(screen.getByRole("option", { name: /S02E01/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /S01E01/ })).toBeNull();

    fireEvent.change(screen.getByLabelText("Episode"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onSubmit).toHaveBeenCalledWith({ kind: "episode", episode_code: "S02E03" });
  });
});
