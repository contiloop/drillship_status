import { Drillship } from '../types';

export type ComputedStatus = 'Active' | 'Idle' | 'Warm-Stacked' | 'Cold-Stacked';

/**
 * 오늘 날짜 기준으로 선박의 상태를 자동 계산합니다.
 *
 * 로직:
 * - 공식 보고서의 상태는 statusAsOf 기준일까지 유효합니다.
 * - 보고서 이후 시작한 Firm 계약이 오늘 유효하면 Idle/Stacked를 Active로
 *   전환합니다. 보고서가 이미 반영한 이전 계약으로는 상태를 덮어쓰지 않습니다.
 * - Active로 보고됐더라도 오늘 유효한 Firm 계약이 없으면 Idle로 전환합니다.
 * - statusAsOf가 없는 레거시 데이터는 비가동 상태를 보수적으로 유지합니다.
 */
export function getComputedStatus(ship: Drillship, today: Date = new Date()): ComputedStatus {
  const todayStr = today.toISOString().split('T')[0];
  const currentFirmContracts = ship.contracts.filter(contract => {
    return contract.status === 'Firm' && contract.startDate <= todayStr && contract.endDate >= todayStr;
  });

  if (ship.status === 'Active') {
    return currentFirmContracts.length > 0 ? 'Active' : 'Idle';
  }

  if (!ship.statusAsOf || todayStr <= ship.statusAsOf) {
    return ship.status;
  }

  const beganAfterStatusSnapshot = currentFirmContracts.some(contract => {
    return contract.startDate > ship.statusAsOf!;
  });

  return beganAfterStatusSnapshot ? 'Active' : ship.status;
}

/**
 * 선박의 계약 갭(idle 기간)을 계산합니다.
 * 타임라인에서 계약 없는 기간을 표시할 때 사용
 * Note: Warm-Stacked와 Idle을 구분하지 않음. 모두 계약 사이의 gap.
 */
export interface IdlePeriod {
  startDate: string;
  endDate: string;
  status: 'Idle';
}

export function getIdlePeriods(
  ship: Drillship,
  timelineStart: Date,
  timelineEnd: Date
): IdlePeriod[] {
  const periods: IdlePeriod[] = [];

  // 계약을 시작일 기준으로 정렬
  const sortedContracts = ship.contracts.filter(contract => contract.status === 'Firm').sort((a, b) =>
    a.startDate.localeCompare(b.startDate)
  );

  const timelineStartStr = timelineStart.toISOString().split('T')[0];
  const timelineEndStr = timelineEnd.toISOString().split('T')[0];

  if (sortedContracts.length === 0) {
    return periods;
  }

  // 첫 계약 전 기간
  if (sortedContracts[0].startDate > timelineStartStr) {
    periods.push({
      startDate: timelineStartStr,
      endDate: sortedContracts[0].startDate,
      status: 'Idle'
    });
  }

  // 계약 사이 갭
  for (let i = 0; i < sortedContracts.length - 1; i++) {
    const currentEnd = sortedContracts[i].endDate;
    const nextStart = sortedContracts[i + 1].startDate;

    if (currentEnd < nextStart) {
      periods.push({
        startDate: currentEnd,
        endDate: nextStart,
        status: 'Idle'
      });
    }
  }

  // 마지막 계약 후 기간
  const lastContract = sortedContracts[sortedContracts.length - 1];
  if (lastContract.endDate < timelineEndStr) {
    periods.push({
      startDate: lastContract.endDate,
      endDate: timelineEndStr,
      status: 'Idle'
    });
  }

  return periods;
}

/** Firm 계약 구간을 합집합으로 계산해 월 정밀도 경계의 중복을 제거합니다. */
export function countFirmCoveredDays(ship: Drillship, rangeStart: Date, rangeEnd: Date): number {
  const from = rangeStart.toISOString().slice(0, 10);
  const to = rangeEnd.toISOString().slice(0, 10);
  const intervals = ship.contracts
    .filter(contract => contract.status === 'Firm' && contract.endDate >= from && contract.startDate <= to)
    .map(contract => [contract.startDate < from ? from : contract.startDate, contract.endDate > to ? to : contract.endDate] as const)
    .sort((a, b) => a[0].localeCompare(b[0]));

  if (intervals.length === 0) return 0;
  let total = 0;
  let currentStart = intervals[0][0];
  let currentEnd = intervals[0][1];
  const dayMs = 86_400_000;
  const utc = (value: string) => Date.parse(`${value}T00:00:00Z`);

  for (const [start, end] of intervals.slice(1)) {
    if (utc(start) <= utc(currentEnd) + dayMs) {
      if (end > currentEnd) currentEnd = end;
      continue;
    }
    total += Math.floor((utc(currentEnd) - utc(currentStart)) / dayMs) + 1;
    currentStart = start;
    currentEnd = end;
  }
  return total + Math.floor((utc(currentEnd) - utc(currentStart)) / dayMs) + 1;
}
