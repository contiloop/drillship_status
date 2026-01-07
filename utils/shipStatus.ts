import { Drillship } from '../types';

export type ComputedStatus = 'Active' | 'Idle' | 'Cold-Stacked';

/**
 * 오늘 날짜 기준으로 선박의 상태를 자동 계산합니다.
 *
 * 로직:
 * 1. 원본 status가 Cold-Stacked → Cold-Stacked (수동 지정 유지)
 * 2. 현재 계약 진행 중 → Active
 * 3. 그 외 (계약 사이 gap, 미래 계약 대기) → Idle
 *
 * Note: Warm-Stacked는 별도로 구분하지 않음. 계약 사이의 gap은 모두 Idle로 처리.
 */
export function getComputedStatus(ship: Drillship, today: Date = new Date()): ComputedStatus {
  // Cold-Stacked는 원본 status에서 가져옴 (수동 지정)
  if (ship.status === 'Cold-Stacked') {
    return 'Cold-Stacked';
  }

  const todayStr = today.toISOString().split('T')[0];

  // 현재 진행 중인 계약이 있는지 확인
  const hasActiveContract = ship.contracts.some(contract => {
    return contract.startDate <= todayStr && contract.endDate >= todayStr;
  });

  if (hasActiveContract) {
    return 'Active';
  }

  // 계약 사이 gap 또는 미래 계약 대기 중
  return 'Idle';
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
  const sortedContracts = [...ship.contracts].sort((a, b) =>
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
