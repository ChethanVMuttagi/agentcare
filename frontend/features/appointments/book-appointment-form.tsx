"use client";

import { useActionState, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { initialAppointmentFormState } from "@/features/appointments/action-state";
import { createAppointmentAction } from "@/features/appointments/actions";
import { apiErrorFromResponse, describeError } from "@/lib/errors";
import { cn, formatTime } from "@/lib/utils";
import type { AvailableTimeSlotResponse, AvailableTimesResponse, DepartmentResponse, PractitionerResponse } from "@/types/api";

const DURATION_OPTIONS = [15, 30, 45, 60];

export function BookAppointmentForm({
  organizationId,
  departments,
  practitioners,
  defaultPatientId,
}: {
  organizationId: string;
  departments: DepartmentResponse[];
  practitioners: PractitionerResponse[];
  defaultPatientId?: string;
}) {
  const [departmentId, setDepartmentId] = useState(departments[0]?.id ?? "");
  const [practitionerId, setPractitionerId] = useState(practitioners[0]?.id ?? "");
  const [date, setDate] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(30);
  const [slots, setSlots] = useState<AvailableTimeSlotResponse[] | null>(null);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsError, setSlotsError] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<AvailableTimeSlotResponse | null>(null);

  const [state, formAction, pending] = useActionState(
    createAppointmentAction.bind(null, organizationId),
    initialAppointmentFormState,
  );

  const canSearch = Boolean(departmentId && practitionerId && date && durationMinutes);

  async function findAvailableTimes() {
    setSlotsLoading(true);
    setSlotsError(null);
    setSelectedSlot(null);
    try {
      const url = new URL(
        `/api/backend/organizations/${organizationId}/practitioners/${practitionerId}/available-times`,
        window.location.origin,
      );
      url.searchParams.set("department_id", departmentId);
      url.searchParams.set("date", date);
      url.searchParams.set("duration_minutes", String(durationMinutes));

      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) {
        throw await apiErrorFromResponse(response);
      }
      const data = (await response.json()) as AvailableTimesResponse;
      setSlots(data.available_times);
    } catch (error) {
      setSlots(null);
      setSlotsError(describeError(error));
    } finally {
      setSlotsLoading(false);
    }
  }

  if (departments.length === 0 || practitioners.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Booking requires at least one department and one practitioner to be set up for this
        organization first.
      </p>
    );
  }

  return (
    <form action={formAction} className="max-w-xl space-y-5">
      <input type="hidden" name="patient_id" value={defaultPatientId ?? ""} />
      <input type="hidden" name="department_id" value={departmentId} />
      <input type="hidden" name="practitioner_id" value={practitionerId} />
      <input type="hidden" name="start_at" value={selectedSlot?.start_at ?? ""} />
      <input type="hidden" name="duration_minutes" value={durationMinutes} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="department">Department</Label>
          <Select
            id="department"
            value={departmentId}
            onChange={(event) => {
              setDepartmentId(event.target.value);
              setSlots(null);
            }}
          >
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="practitioner">Practitioner</Label>
          <Select
            id="practitioner"
            value={practitionerId}
            onChange={(event) => {
              setPractitionerId(event.target.value);
              setSlots(null);
            }}
          >
            {practitioners.map((practitioner) => (
              <option key={practitioner.id} value={practitioner.id}>
                {practitioner.first_name} {practitioner.last_name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="date">Date</Label>
          <Input
            id="date"
            type="date"
            value={date}
            onChange={(event) => {
              setDate(event.target.value);
              setSlots(null);
            }}
          />
        </div>
        <div>
          <Label htmlFor="duration">Duration</Label>
          <Select
            id="duration"
            value={durationMinutes}
            onChange={(event) => {
              setDurationMinutes(Number(event.target.value));
              setSlots(null);
            }}
          >
            {DURATION_OPTIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes} minutes
              </option>
            ))}
          </Select>
        </div>
      </div>

      <Button type="button" variant="secondary" disabled={!canSearch || slotsLoading} onClick={findAvailableTimes}>
        {slotsLoading ? <Spinner /> : null}
        Find available times
      </Button>

      {slotsError ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {slotsError}
        </p>
      ) : null}

      {slots ? (
        slots.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No open slots for that day. Try a different date.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {slots.map((slot) => {
              const active = selectedSlot?.start_at === slot.start_at;
              return (
                <button
                  key={slot.start_at}
                  type="button"
                  onClick={() => setSelectedSlot(slot)}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-sm font-medium",
                    active
                      ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
                      : "border-slate-300 text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800",
                  )}
                >
                  {formatTime(slot.start_at)}
                </button>
              );
            })}
          </div>
        )
      ) : null}

      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}

      <Button type="submit" disabled={!selectedSlot || pending}>
        {pending ? "Booking…" : "Book appointment"}
      </Button>
    </form>
  );
}
